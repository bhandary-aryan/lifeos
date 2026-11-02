"""Guards the production static-files pipeline: dev serves anything, but WhiteNoise's manifest storage fails the build
(or every page at runtime) if a file or a referenced source map is missing. This test runs the real thing."""
import re
from pathlib import Path

import pytest
from django.contrib.staticfiles.storage import staticfiles_storage
from django.core.management import call_command

ROOT = Path(__file__).resolve().parent.parent


def test_vendored_scripts_do_not_reference_missing_source_maps():
    for js in (ROOT / "static" / "vendor").glob("*.js"):
        assert "sourceMappingURL" not in js.read_text(errors="ignore"), f"{js.name} references a source map we do not ship (run npm run vendor)"


@pytest.fixture
def prod_static(settings, tmp_path):
    settings.STATIC_ROOT = str(tmp_path / "static")
    settings.STORAGES = {**settings.STORAGES, "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"}}
    call_command("collectstatic", interactive=False, verbosity=0)
    return tmp_path / "static"


def referenced_static_paths():
    found = set()
    for tpl in (ROOT / "templates").rglob("*.html"):
        found |= set(re.findall(r"""{%\s*static\s+['"]([^'"]+)['"]\s*%}""", tpl.read_text()))
    return found


def test_collectstatic_builds_a_manifest_and_every_template_reference_resolves(prod_static):
    assert (prod_static / "staticfiles.json").exists()
    paths = referenced_static_paths()
    assert {"css/app.css", "vendor/htmx.min.js", "vendor/chart.umd.js", "vendor/lucide.min.js", "vendor/alpine.min.js", "js/chart-theme.js"} <= paths
    for p in sorted(paths):
        url = staticfiles_storage.url(p)                       # raises ValueError if the manifest has no entry
        assert re.search(r"\.[0-9a-f]{12}\.", url), (p, url)   # content-hashed, so browsers can cache forever safely


def test_pages_render_under_manifest_storage(prod_static, auth_client):
    from django.urls import reverse
    for name in ("dashboard:home", "finance:overview", "reflections:insights", "interests:list"):
        r = auth_client.get(reverse(name))
        assert r.status_code == 200 and re.search(rb"/static/css/app\.[0-9a-f]{12}\.css", r.content), name


def test_error_pages_render_under_manifest_storage(prod_static, client, settings):
    settings.DEBUG = False
    r = client.get("/nope/")
    assert r.status_code == 404 and re.search(rb"/static/css/app\.[0-9a-f]{12}\.css", r.content)


def test_every_page_with_charts_applies_the_theme_to_chart_js():
    for tpl in (ROOT / "templates").rglob("*.html"):
        text = tpl.read_text()
        if "chart.umd.js" in text:
            assert text.index("chart.umd.js") < text.index("chart-theme.js"), f"{tpl.name}: chart-theme.js must load right after Chart.js"


def test_chart_scripts_use_theme_colours_and_whole_number_scales():
    """Fixed hex series colours were hard to see on the dark theme; count charts must not show fractional ticks."""
    import re
    for name in ("reflections/insights.html", "finance/overview.html", "finance/reports.html"):
        script = (ROOT / "templates" / name).read_text().split("<script>")[-1]
        body = re.sub(r"C = \(window\.LifeosChart[^;]*;", "", script)          # the documented fallback palette
        assert not re.search(r'(backgroundColor|borderColor):\s*"#', body), f"{name}: hard-coded series colour"
    insights = (ROOT / "templates/reflections/insights.html").read_text()
    assert insights.count("options: optCount") == 2 and "precision: 0" in insights
