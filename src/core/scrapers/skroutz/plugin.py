"""Declarative descriptor for the Skroutz scraper."""

from core.scrapers.base.plugin import ClassRef, PluginDefinition


PLUGIN = PluginDefinition(
    display_name="Skroutz",
    domains=("skroutz.gr", "skroutz.cy", "skroutz.ro", "skroutz.bg", "skroutz.de"),
    client=ClassRef(".client", "SkroutzClient"),
    storage=ClassRef(".storage", "SkroutzDataManager"),
)
