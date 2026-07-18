import dataclasses
import datetime
import json
import os
import shutil
from abc import ABC, abstractmethod
from collections import defaultdict
from typing import Any, TYPE_CHECKING

from core.scrapers.base.model import BaseTrackedItem
from core.scrapers.base.url import clean_url
from core.constants import TIMESTAMP_FORMAT
from core.exceptions import StorageFileError
from core.utils import parse_price, write_json_atomically

if TYPE_CHECKING:
    from core.scrapers.base.plugin import RegisteredPlugin
    from core.scrapers.base.settings import ResolvedSettings


class BaseDataManager(ABC):
    """Abstract base class for storing the items one scraper tracks.

    This is the *storage backend* contract, not a fully generic key-value store: it
    models a **tracked row carrying a URL, stable identity, and ``target_price``**, which
    is this application's domain. That assumption is baked into the shared helpers —
    ``has_valid_target_price``, ``is_scrapable_item`` and ``_url_on_supported_domain``
    are about product URLs and target prices — so a subclass inherits those semantics;
    it does not get a blank slate.

    Subclasses must call ``super().__init__(filepath, plugin, settings)`` in their
    ``__init__`` and use ``self.filepath`` for all file operations. The manager
    is given its owning registered plugin so that domain matching resolves
    through the registered plugin's normalized ``domains`` (the single source of truth)
    rather than importing a concrete plugin, and its target's resolved
    ``self.settings`` so a store-specific setting is readable at scrape time
    (e.g. ``self.settings.get("region")``) without extra plumbing. Both are
    injected by the registry.

    Note:
        Almost every store backs its data with a JSON file and should extend
        :class:`JsonProductDataManager` (below), not this class directly — it
        implements the entire JSON lifecycle (load/validate, cache-and-merge updates,
        atomic save, dedup) and leaves only ``_matches_product_path`` (the
        store-specific URL-path rule) abstract. Subclass this class directly only for
        a non-file backend (a database or remote API) — and even then you are still
        implementing the same tracked-item contract, just against a different store.
    """
    def __init__(self, filepath: str, plugin: "RegisteredPlugin | None" = None,
                 settings: "ResolvedSettings | None" = None) -> None:
        """Initializes the data manager.

        Args:
            filepath (str): The path to the storage file.
            plugin (RegisteredPlugin | None): The owning plugin, used to resolve the
                supported domains for URL matching. Injected by the registry.
            settings (ResolvedSettings | None): The target's resolved settings,
                injected by the registry so a subclass can read a store-specific
                setting at scrape time (e.g. ``self.settings.get("region")``). ``None``
                when constructed outside the registry (e.g. a unit test).
        """
        self.filepath = filepath
        self.plugin = plugin
        self.settings = settings

    # ------------------------------------------------------------------
    # Core lifecycle – must be implemented by every subclass
    # ------------------------------------------------------------------

    @abstractmethod
    def load(self) -> dict[str, Any]:
        """Loads and validates data from the storage into memory.

        This is the single read/validation entry point for a target. Implementations
        must verify the source is present and well-formed, populate their in-memory
        state, and raise StorageFileError on any problem. After a successful call,
        ``get_items``, ``get_item_count`` and ``get_faulty_indices`` reflect the data.

        Returns:
            dict[str, Any]: The parsed data representing tracked items.

        Raises:
            StorageFileError: If the source is missing, unreadable, or malformed.
        """
        pass

    @abstractmethod
    def save(self) -> None:
        """Persists the current in-memory state back to storage.

        Implementations should apply any pending updates cached via
        ``update_item`` and write the result to the underlying store.
        The mechanism (atomic file swap, database transaction, etc.) is
        an implementation detail.
        """
        pass

    # ------------------------------------------------------------------
    # Item access
    # ------------------------------------------------------------------

    @abstractmethod
    def update_item(self, item: BaseTrackedItem, **updates: Any) -> None:
        """Cache field updates for a parsed item.

        Updates are not written to persistent storage until ``save``
        is called. Any keyword argument is accepted so that each
        scraper can store whatever fields it needs (e.g. ``last_price``,
        ``stock_status``, ``shipping_cost``).

        Args:
            item (BaseTrackedItem): The item whose state is being updated.
            **updates: Arbitrary field updates to cache for the item.
        """
        pass

    @abstractmethod
    def get_items(self) -> list[Any]:
        """Returns the raw list of items from the loaded data.

        Returns:
            list[Any]: Raw row values. A configuration boundary may contain malformed
                non-object JSON values; callers must validate a row before parsing it.
        """
        pass

    def get_item_count(self) -> int:
        """Returns the number of tracked items without materializing the full list.

        The default implementation delegates to ``get_items()``.
        Subclasses backed by a database or API may override this for
        efficiency.

        Returns:
            int: The total number of items.
        """
        return len(self.get_items())

    # ------------------------------------------------------------------
    # Parsing & Validation
    # ------------------------------------------------------------------

    @abstractmethod
    def parse_item(self, data: dict[str, Any]) -> BaseTrackedItem:
        """Parses a dictionary into a BaseTrackedItem.

        Args:
            data (dict[str, Any]): The item data.

        Returns:
            BaseTrackedItem: The parsed item.
        """
        pass

    def get_faulty_indices(self) -> list[int]:
        """Returns the 1-based indices of loaded items that fail validation.

        The default implementation derives the result from the already-loaded
        items via ``is_valid_item``; subclasses backed by a database or API may
        override it. Call after ``load`` so the in-memory data is populated.

        Returns:
            list[int]: 1-based indices of items for which ``is_valid_item`` is False.
        """
        return [i + 1 for i, item in enumerate(self.get_items()) if not self.is_valid_item(item)]

    @abstractmethod
    def is_valid_item(self, item: Any) -> bool:
        """Validates an individual item's data structure and content.

        Args:
            item: A raw stored row; non-object JSON values are invalid.

        Returns:
            bool: True if the item is valid, False otherwise.
        """
        pass

    @abstractmethod
    def is_scrapable_item(self, item: Any) -> bool:
        """Checks if the item has a valid, properly formatted URL.

        Args:
            item: A raw stored row; non-object JSON values are not scrapable.

        Returns:
            bool: True if the item can be scraped, False otherwise.
        """
        pass

    @abstractmethod
    def clean_storage(self) -> None:
        """Performs pre-scrape cleanup on the storage data (e.g., removing duplicates)."""
        pass

    # ------------------------------------------------------------------
    # Shared concrete helpers
    # ------------------------------------------------------------------

    def has_valid_target_price(self, item: Any) -> bool:
        """Checks if an item has a valid, non-negative ``target_price``.

        Args:
            item: A raw stored row; only mappings can carry a target price.

        Returns:
            bool: True if ``target_price`` exists and is a valid non-negative number.
        """
        if not isinstance(item, dict) or "target_price" not in item:
            return False

        price = parse_price(item.get("target_price"))
        if price is None or price < 0:
            return False

        return True

    def _url_on_supported_domain(self, url: str) -> bool:
        """Returns True if the URL's host is one this plugin handles.

        Delegates to ``plugin.matches_url`` — the single place the supported-domain
        match is performed — so domain matching never drifts between routing
        (registry) and storage validation. Returns False when no plugin was injected.

        Args:
            url (str): The URL to check.

        Returns:
            bool: True if the URL is on a supported domain.
        """
        if self.plugin is None:
            return False
        return self.plugin.matches_url(url)


class JsonProductDataManager(BaseDataManager):
    """Generic data manager for a JSON file holding a list of tracked items.

    Implements the entire storage lifecycle shared by every JSON-file-backed
    scraper: load/validate, cache-and-merge updates, atomic save, and
    duplicate cleanup. The file is treated both as configuration (the tracked
    items and their target prices) and as state (the scraper writes back the
    latest price and check timestamp).

    Subclasses only need to declare two class attributes and implement one
    store-specific method:

        class FooDataManager(JsonProductDataManager):
            MODEL = FooItem        # a BaseTrackedItem subclass
            ROOT_KEY = "products"  # top-level JSON key holding the item list

            def _matches_product_path(self, url): ...  # store URL-path rule

    Everything else (parsing, validation, dedup, persistence, and the
    supported-domain check) is inherited.
    """

    #: The :class:`BaseTrackedItem` subclass that ``parse_item`` instantiates.
    MODEL: type[BaseTrackedItem] = BaseTrackedItem
    #: The top-level JSON key whose value is the list of item dictionaries.
    ROOT_KEY: str = "products"

    def __init__(self, filepath: str, plugin: "RegisteredPlugin | None" = None,
                 settings: "ResolvedSettings | None" = None) -> None:
        """Initializes the manager with the JSON file path.

        Args:
            filepath (str): The path to the JSON storage/config file.
            plugin (RegisteredPlugin | None): The owning plugin (see BaseDataManager).
            settings (ResolvedSettings | None): The target's resolved settings
                (see BaseDataManager); injected by the registry.
        """
        super().__init__(filepath, plugin, settings)
        self._data: dict[str, Any] = {}
        self._updates: dict[str, dict[str, Any]] = {}

    @property
    def _config_label(self) -> str:
        """Returns a human-readable ``config/<file>`` label for error messages."""
        return f"config/{os.path.basename(self.filepath)}"

    def _save_json_atomically(self, data: dict[str, Any]) -> None:
        """Writes data to the JSON file atomically using a temp-file swap.

        Delegates to the shared :func:`core.utils.write_json_atomically` (temp-file
        write then ``os.replace``) - the single atomic-JSON writer - and adapts its
        ``OSError`` to the storage layer's fatal ``StorageFileError``. This is the sole
        writer for the JSON backend.

        Args:
            data (dict[str, Any]): The data to serialize as JSON.

        Raises:
            StorageFileError: If the write operation fails.
        """
        try:
            write_json_atomically(self.filepath, data)
        except OSError as e:
            raise StorageFileError(str(e))

    def load(self) -> dict[str, Any]:
        """Loads and validates the items data from the JSON file.

        Performs the full pre-scrape validation (directory, existence, permissions
        and JSON structure) and populates the in-memory state in one pass.

        Returns:
            dict[str, Any]: The parsed JSON data.

        Raises:
            StorageFileError: If the file is missing, has wrong permissions, or
                contains invalid JSON (or lacks the ``ROOT_KEY`` list).
        """
        config_dir = os.path.dirname(self.filepath)
        if config_dir and not os.path.exists(config_dir):
            try:
                os.makedirs(config_dir, exist_ok=True)
            except OSError as e:
                raise StorageFileError(f"Could not create {self._config_label} directory: {e}")

        if not os.path.exists(self.filepath) or not os.path.isfile(self.filepath):
            raise StorageFileError(f"The {self._config_label} file is missing or not a file")

        if not os.access(self.filepath, os.R_OK | os.W_OK):
            raise StorageFileError(f"The {self._config_label} file has wrong permissions")

        try:
            with open(self.filepath, 'r') as file:
                data = json.load(file)
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise StorageFileError(f"The {self._config_label} file contains invalid JSON format")

        self._validate_document(data)

        self._data = data
        return self._data

    def _validate_document(self, data: Any, *, while_saving: bool = False) -> None:
        """Validates the fatal top-level JSON shape shared by load and save.

        Individual list entries are deliberately not checked here: malformed rows are
        recoverable and reported through ``get_faulty_indices``. Only a document that
        cannot represent this manager's item collection is a target-level failure.
        """
        if isinstance(data, dict) and isinstance(data.get(self.ROOT_KEY), list):
            return
        if while_saving:
            raise StorageFileError(
                f"The {self._config_label} file changed to an invalid structure while saving"
            )
        raise StorageFileError(f"The {self._config_label} file contains invalid JSON format")

    def update_item(self, item: BaseTrackedItem, **updates: Any) -> None:
        """Cache updates using the model's single stable identity key.

        Every update key must name a field of :attr:`MODEL` (the store's
        :class:`BaseTrackedItem` subclass). A key that does not is a programming error -
        it would be persisted into the JSON row and then silently dropped on the next
        ``from_dict`` read - so it is rejected loudly here rather than written as junk.

        Args:
            item (BaseTrackedItem): The parsed item to update.
            **updates: Field updates (e.g. ``last_price=12.5``,
                ``last_checked="11-06-2026 01:00:00"``); each key must be a ``MODEL`` field.

        Raises:
            TypeError: If the item is not an instance of this manager's model.
            ValueError: If an update key is not a field of ``MODEL``.
        """
        if type(item) is not self.MODEL:
            raise TypeError(
                f"update_item requires {self.MODEL.__name__}, got {type(item).__name__}."
            )
        allowed = {field.name for field in dataclasses.fields(self.MODEL)}
        unknown = [key for key in updates if key not in allowed]
        if unknown:
            raise ValueError(
                f"update_item received unknown field(s) {unknown} for {self.MODEL.__name__}; "
                f"valid fields are {sorted(allowed)}."
            )

        key = item.identity_key()
        if key in self._updates:
            self._updates[key].update(updates)
        else:
            self._updates[key] = dict(updates)

    def get_items(self) -> list[Any]:
        """Returns the raw list of stored row values.

        Note:
            The config's top-level ``settings`` block is **not** read here. Settings are
            a config-file concept resolved import-light through
            :meth:`core.scrapers.registry.ScraperRegistry.resolve_all_settings` (and the
            per-setting ``resolve_*`` helpers), so they are read uniformly for any
            backend rather than through this JSON-only manager. This manager owns only
            the *item* lifecycle; it never reads or writes ``settings``.
        """
        return self._data.get(self.ROOT_KEY, [])

    def _clean_products(self, products: list[Any]) -> list[Any]:
        """Cleans mapping rows while preserving malformed rows at their exact indices."""
        groups = defaultdict(list)

        for i, product in enumerate(products):
            if not isinstance(product, dict):
                # Every malformed row remains independently visible at its original
                # index; repeated null/scalar rows must never be deduplicated away.
                groups[("invalid-row", i)].append((i, product))
                continue
            if "skip" not in product:
                product["skip"] = False

            url = str(product.get("url", ""))
            # Group by row identity if scrapable, otherwise fallback to string representation of raw url
            row_key = self.parse_item(product).identity_key() if self.is_scrapable_item(product) else url
            groups[row_key].append((i, product))

        items_to_keep = set()
        for _identity, group in groups.items():
            if len(group) == 1:
                items_to_keep.add(group[0][0])
                continue

            valid_indices = [i for i, p in group if self.is_valid_item(p)]
            if valid_indices:
                items_to_keep.add(valid_indices[0])
            else:
                scrapable_indices = [i for i, p in group if self.is_scrapable_item(p)]
                if scrapable_indices:
                    items_to_keep.add(scrapable_indices[0])
                else:
                    for i, p in group:
                        items_to_keep.add(i)

        cleaned_products = []
        for i, product in enumerate(products):
            if i in items_to_keep:
                if isinstance(product, dict) and self.is_scrapable_item(product):
                    product["url"] = clean_url(product.get("url", ""))
                cleaned_products.append(product)

        return cleaned_products

    def clean_storage(self) -> None:
        """Normalizes the in-memory item list (dedup + URL cleaning) before scraping.

        Operates only on the in-memory snapshot so the scrape loop iterates a clean,
        de-duplicated list. Persistence is deferred entirely to ``save()`` — the sole
        writer — which re-reads the file and re-cleans it, absorbing any external
        edits made during the run. Keeping this in-memory avoids writing the config
        file twice per run.
        """
        if self.ROOT_KEY in self._data:
            self._data[self.ROOT_KEY] = self._clean_products(self._data[self.ROOT_KEY])

    def _backup_corrupt_file(self) -> None:
        """Best-effort copy of a corrupt config file to a sibling ``.corrupt`` file.

        Called when the on-disk JSON is unparseable at save time, just before it is
        overwritten with the self-healed in-memory snapshot, so the user's original
        (corrupt) content is preserved for inspection rather than silently lost.
        Any failure here is swallowed: the backup is a courtesy and must never
        prevent the actual save.
        """
        try:
            shutil.copyfile(self.filepath, self.filepath + ".corrupt")
        except OSError:
            pass

    def save(self) -> None:
        """Applies pending updates and saves the data back to the JSON file atomically.

        Re-reads the file from disk to merge with any external edits made
        during the scraping run, then applies cached updates and performs
        a final duplicate cleanup before writing.

        If the file is present but unreadable (``OSError``) this raises
        :class:`StorageFileError` — mirroring ``load`` — so the caller can report it
        instead of an unhandled error aborting the run. If it is present but contains
        invalid JSON or text encoding, the corrupt content is backed up to a sibling
        ``.corrupt`` file and the in-memory snapshot is rewritten in its place. A valid
        JSON document with the wrong top-level shape is preserved and rejected.

        Raises:
            StorageFileError: If the file cannot be read, or the atomic write fails.
        """
        fresh_data: Any = {}
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, 'r') as file:
                    fresh_data = json.load(file)
            except (json.JSONDecodeError, UnicodeError):
                # The on-disk file became invalid JSON during the run. Preserve the
                # corrupt bytes (best-effort) so the user's data is never silently
                # lost, then self-heal by rewriting from the in-memory snapshot.
                self._backup_corrupt_file()
                fresh_data = self._data
            except OSError as e:
                # The file exists but cannot be read (permissions, I/O error, replaced
                # by a directory). Surface as StorageFileError so the orchestrator's
                # save guard handles it, rather than letting an unhandled OSError
                # abort the whole run.
                raise StorageFileError(f"Could not read {self._config_label} while saving: {e}")
        else:
            fresh_data = self._data

        self._validate_document(fresh_data, while_saving=True)

        for product in fresh_data[self.ROOT_KEY]:
            if not isinstance(product, dict):
                continue
            row_key = self.parse_item(product).identity_key()

            if row_key in self._updates:
                for key, value in self._updates[row_key].items():
                    product[key] = value

        # We also need to clean duplicates when saving to ensure any user edits during scraping are handled.
        fresh_data[self.ROOT_KEY] = self._clean_products(fresh_data[self.ROOT_KEY])

        self._data = fresh_data
        self._save_json_atomically(self._data)

    def parse_item(self, data: dict[str, Any]) -> BaseTrackedItem:
        """Parses a dictionary into a ``MODEL`` instance."""
        return self.MODEL.from_dict(data)

    def is_valid_item(self, item: Any) -> bool:
        """Validates an item dictionary.

        Args:
            item: A raw stored row; non-object JSON values are invalid.

        Returns:
            bool: True if the item has a name, a scrapable URL, and a valid target price.
        """
        if not isinstance(item, dict):
            return False

        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            return False

        if not self.is_scrapable_item(item):
            return False

        if not self.has_valid_target_price(item):
            return False

        if "skip" in item and not isinstance(item["skip"], bool):
            return False

        if "last_price" in item:
            last_price = parse_price(item["last_price"])
            if last_price is None or last_price < 0:
                return False

        if "last_checked" in item:
            last_checked = item["last_checked"]
            if not isinstance(last_checked, str):
                return False
            if last_checked:
                try:
                    datetime.datetime.strptime(last_checked, TIMESTAMP_FORMAT)
                except ValueError:
                    return False

        return True

    def is_scrapable_item(self, item: Any) -> bool:
        """Checks whether the item has a scrapable product URL.

        Composes the shared supported-domain check (inherited, driven by the
        plugin) with the store-specific path rule (:meth:`_matches_product_path`),
        so a concrete plugin declares only the path shape. Override this method
        entirely only for stores whose scrappability cannot be expressed as
        "supported domain + path shape".

        Args:
            item: A raw stored row; non-object JSON values are not scrapable.

        Returns:
            bool: True if the URL is on a supported domain and its path matches.
        """
        if not isinstance(item, dict):
            return False
        url = item.get("url", "")
        return self._url_on_supported_domain(url) and self._matches_product_path(url)

    @abstractmethod
    def _matches_product_path(self, url: str) -> bool:
        """Returns True if the URL path matches this store's product-page shape.

        Called only after the domain has been confirmed supported, so the URL is
        guaranteed to be a non-empty string on a supported domain; implementations
        need only inspect the path (e.g. a numeric product-id segment).

        Args:
            url (str): A URL already confirmed to be on a supported domain.

        Returns:
            bool: True if the path matches this store's product-page shape.
        """
        ...
