"""
Unit tests for the Qt-load failure guidance in ``dti_alps.gui``.

Importing ``dti_alps.gui`` is safe here: the package ``__init__`` is Qt-free and
imports PySide6 only inside the dependency check, so these tests run without a
working Qt install -- which is the whole point, since they describe what the app
says when Qt is exactly that: installed but unloadable.
"""

from dti_alps.gui import RELEASES_URL, qt_load_failure_message


class TestQtLoadFailureMessage:
    """Tests for qt_load_failure_message()."""

    def test_includes_the_underlying_loader_error(self):
        """The raw linker message is surfaced -- it is what a user searches for."""
        exc = ImportError("libharfbuzz.so.0: undefined symbol: FT_Get_Colorline_Stops")
        lines = qt_load_failure_message(exc)

        assert any("FT_Get_Colorline_Stops" in line for line in lines)

    def test_disclaims_dti_alps_as_the_cause(self):
        """The traceback points at gui/app.py, so the message must say otherwise."""
        lines = qt_load_failure_message(ImportError("boom"))
        text = "\n".join(lines).lower()

        assert "not with dti-alps itself" in text

    def test_offers_all_three_escape_routes(self):
        """pipx (isolated), the AppImage (bundled Qt), and the in-conda repair."""
        text = "\n".join(qt_load_failure_message(ImportError("boom")))

        assert "pipx install dti-alps" in text
        assert RELEASES_URL in text
        assert "freetype" in text and "harfbuzz" in text

    def test_returns_lines_without_trailing_newlines(self):
        """Caller prints line by line; embedded newlines would double-space it."""
        lines = qt_load_failure_message(ImportError("boom"))

        assert all("\n" not in line for line in lines)
