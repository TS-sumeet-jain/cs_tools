from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, Optional
import functools as ft
import importlib.util
import logging
import pathlib
import sys
import warnings

from packaging.requirements import Requirement
import pydantic
import sqlalchemy as sa
import sqlmodel

from cs_tools import errors, utils
from cs_tools.datastructures import ValidatedSQLModel, _GlobalModel, _GlobalSettings
from cs_tools.updater._updater import cs_tools_venv

if TYPE_CHECKING:
    from cs_tools.sync.types import TableRows

log = logging.getLogger(__name__)
_registry: set[str] = set()


class PipRequirement(_GlobalModel):
    requirement: Requirement
    pip_args: list[str] = []  # noqa: RUF012

    @pydantic.model_validator(mode="before")
    @classmethod
    def json_tuple_to_dict(cls, data: Any) -> Any:
        if isinstance(data, str):
            return {"requirement": Requirement(data)}

        requirement, *args = data
        return {"requirement": Requirement(requirement), "pip_args": args}

    def __str__(self) -> str:
        return f"{self.requirement} {' '.join(self.pip_args)}"


class SyncerManifest(_GlobalModel):
    name: str
    syncer_class: str
    requirements: list[PipRequirement] = []  # noqa: RUF012

    def import_syncer_class(self, fp: pathlib.Path) -> type[Syncer]:
        __name__ = f"cs_tools_{fp.parent.stem}_syncer"  # noqa: A001
        __file__ = fp
        __path__ = [fp.parent.as_posix()]

        self.__ensure_pip_requirements__()
        spec = importlib.util.spec_from_file_location(__name__, __file__, submodule_search_locations=__path__)

        if spec is None or spec.loader is None:
            raise errors.CSToolsError(f"Could not import syncer class: {__name__} from {__file__}")

        module = importlib.util.module_from_spec(spec)

        # add to already-loaded modules, so further imports within each directory will work
        sys.modules[__name__] = module

        spec.loader.exec_module(module)
        return getattr(module, self.syncer_class)

    def __ensure_pip_requirements__(self) -> None:
        """Parse the SyncerManifest and install requirements."""
        if utils.determine_editable_install():
            return

        for pip_requirement in self.requirements:
            log.debug(f"Processing requirement: {pip_requirement}")

            if cs_tools_venv.is_package_installed(pip_requirement.requirement.name):
                log.debug("Requirement satisfied, no install necessary")
                continue

            log.info(f"Installing package: {pip_requirement}")
            cs_tools_venv.pip("install", f"{pip_requirement.requirement}", *pip_requirement.pip_args)


class Syncer(_GlobalSettings):
    """A connection to a Data store."""

    __manifest_path__: Optional[pathlib.Path] = None
    __syncer_name__: Optional[str] = None

    def __init_subclass__(cls, is_base_class: bool = False):
        # DEV NOTE: @boonhapus, 2023/12/18
        # Should we swap this out with __pydantic_init_subclass__ to gain access to model-defined fields?
        #
        # https://github.com/pydantic/pydantic/blob/3d2ebef8f76625d6f82e6bf417682fd08ac66296/pydantic/main.py#L607
        super().__init_subclass__()

        if is_base_class:
            return

        # Metaclass-ish wizardry to determine if the Syncer subclass defines the necessary properties.
        if cls.__manifest_path__ is None or cls.__syncer_name__ is None:
            raise NotImplementedError("Syncers must implement both '__syncer_name__' and '__manifest_path__'")

        # Skip attempting to install packages if we've already seen this impl.
        if cls.__syncer_name__ in _registry:
            return

        cls.__init__ = ft.partialmethod(cls.__lifecycle_init__, __original_init__=cls.__init__)  # type: ignore

        # Registration is successful, we can add it to the global now.
        _registry.add(cls.__syncer_name__)

    def __lifecycle_init__(child_self, *a, __original_init__, **kw):
        """Hook into __init__ so we can call our own post-init function."""
        try:
            __original_init__(child_self, *a, **kw)

        except pydantic.ValidationError as e:
            log.debug(e, exc_info=True)
            raise errors.SyncerInitError(pydantic_error=e, proto=e.title) from None

        child_self.__finalize__()

    @property
    def name(self) -> str:
        """Name of the Syncer."""
        assert self.__syncer_name__ is not None
        return self.__syncer_name__

    def __finalize__(self) -> None:
        """Will be called after __init__()."""
        pass

    def __repr__(self) -> str:
        return f"<Syncer to '{self.name}'>"

    __str__ = __repr__

    def load(self, directive: str) -> TableRows:
        """Fetch data from the external data source."""
        raise NotImplementedError(f"There is no default implementation for {self.__class__.__name__}.load")

    def dump(self, directive: str, *, data: TableRows) -> None:
        """Send data to the external data source."""
        raise NotImplementedError(f"There is no default implementation for {self.__class__.__name__}.dump")


class DatabaseSyncer(Syncer, is_base_class=True):
    """A connection to an Database."""

    metadata: sqlmodel.MetaData = sqlmodel.MetaData()
    models: list[type[ValidatedSQLModel]] = []  # noqa: RUF012
    load_strategy: Literal["APPEND", "TRUNCATE", "UPSERT"] = "APPEND"

    # To be defined during __init__() by subclasses of the DatabaseSyncer
    _engine: sa.engine.Engine = None
    _session: sa.orm.Session = None

    @pydantic.field_validator("load_strategy", mode="before")
    def case_insensitive(cls, value: str) -> str:
        return value.upper()

    @property
    def engine(self) -> sa.engine.Engine:
        """The SQLALchemy engine which connects us to our Database."""
        return self._engine

    @property
    def session(self) -> sa.orm.Session:
        """The SQLALchemy session which represents an active connection to our Database."""
        return self._session

    def __finalize__(self) -> None:
        # Metaclass-ish wizardry to determine if the DatabaseSyncer subclass defines the necessary properties.
        if self._engine is None:
            raise NotImplementedError("DatabaseSyncers must implement attribute '_engine'. (sqlalchemy.engine.Engine)")

        with warnings.catch_warnings():
            warnings.filterwarnings(action="ignore", category=sa.exc.SAWarning)

            for model in self.models:
                # If set to None, the schema will be set to that of the schema set on
                # self.metadata. This allows subclasses and instances to override the
                # default behavior.
                #
                # Further reading:
                # https://docs.sqlalchemy.org/en/20/core/metadata.html#sqlalchemy.schema.Table.to_metadata.params.schema
                model.__table__.to_metadata(self.metadata, schema=None)

        log.debug(f"Attempting CREATE TABLE {[t.name for t in self.metadata.sorted_tables]} in {self!r}")
        self.metadata.create_all(self._engine, tables=list(self.metadata.sorted_tables))
        self._session = sa.orm.Session(self._engine)
        self._session.begin()

    def __repr__(self) -> str:
        return f"<DatabaseSyncer to '{self.name}'>"
