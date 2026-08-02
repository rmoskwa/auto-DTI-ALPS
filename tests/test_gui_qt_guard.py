"""
Unit tests for the Qt-load failure guidance in ``dti_alps.gui``.

Importing ``dti_alps.gui`` is safe here: the package ``__init__`` is Qt-free and
imports PySide6 only inside the dependency check, so these tests run without a
working Qt install -- which is the whole point, since they describe what the app
says when Qt is exactly that: installed but unloadable.
"""

from dti_alps.gui import RELEASES_URL, qt_load_failure_message, qt_targeted_remedy

# The report that prompted this guard: conda-forge harfbuzz built against a
# newer FreeType than the one in the same prefix.
FONT_MISMATCH = "libharfbuzz.so.0: undefined symbol: FT_Get_Colorline_Stops"


class TestQtTargetedRemedy:
    """Tests for qt_targeted_remedy() -- one branch per failure family."""

    def test_font_stack_mismatch_suggests_realigning_conda(self):
        remedy = qt_targeted_remedy(FONT_MISMATCH)

        text = "\n".join(remedy)
        assert "freetype harfbuzz" in text
        assert "--update-deps" in text

    def test_font_remedy_carries_no_pinned_version(self):
        """A dated floor would rot into a no-op; --update-deps does the work."""
        text = "\n".join(qt_targeted_remedy(FONT_MISMATCH))

        assert "2.12" not in text

    def test_missing_library_names_the_library_to_install(self):
        remedy = qt_targeted_remedy(
            "libGL.so.1: cannot open shared object file: No such file or directory"
        )

        text = "\n".join(remedy)
        assert "libGL.so.1" in text
        assert "apt" in text
        assert "conda" not in text

    def test_missing_font_library_is_not_read_as_a_mismatch(self):
        """A missing libfreetype needs installing, not realigning -- order matters."""
        remedy = qt_targeted_remedy(
            "libfreetype.so.6: cannot open shared object file: No such file or directory"
        )

        text = "\n".join(remedy)
        assert "libfreetype.so.6" in text
        assert "--update-deps" not in text

    def test_old_glibc_does_not_promise_the_appimage_fixes_it(self):
        """The AppImage bundles Qt, not glibc -- claiming otherwise sends users in circles."""
        remedy = qt_targeted_remedy(
            "/lib/x86_64-linux-gnu/libm.so.6: version `GLIBC_2.29' not found"
        )

        text = "\n".join(remedy)
        assert "does not help" in text
        assert "PySide6==" in text

    def test_glibc_error_naming_a_font_library_is_still_a_glibc_error(self):
        remedy = qt_targeted_remedy(
            "libfreetype.so.6: /lib/libc.so.6: version `GLIBC_2.29' not found"
        )

        assert "glibc" in "\n".join(remedy).lower()
        assert "--update-deps" not in "\n".join(remedy)

    def test_unrecognised_error_gets_no_targeted_step(self):
        """Silence beats misdirecting someone whose failure we cannot classify."""
        assert qt_targeted_remedy("Qt platform plugin could not be initialized") is None


class TestQtLoadFailureMessage:
    """Tests for qt_load_failure_message()."""

    def test_includes_the_underlying_loader_error(self):
        """The raw linker message is surfaced -- it is what a user searches for."""
        lines = qt_load_failure_message(ImportError(FONT_MISMATCH))

        assert any("FT_Get_Colorline_Stops" in line for line in lines)

    def test_disclaims_dti_alps_as_the_cause(self):
        """The traceback points at gui/app.py, so the message must say otherwise."""
        text = "\n".join(qt_load_failure_message(ImportError("boom"))).lower()

        assert "not with dti-alps itself" in text

    def test_always_offers_the_environment_independent_fixes(self):
        """pipx and the AppImage hold whatever the loader complained about."""
        text = "\n".join(qt_load_failure_message(ImportError("something unrecognised")))

        assert "pipx install dti-alps" in text
        assert RELEASES_URL in text

    def test_appends_the_targeted_step_when_the_error_is_recognised(self):
        text = "\n".join(qt_load_failure_message(ImportError(FONT_MISMATCH)))

        assert "  3." in text
        assert "--update-deps" in text

    def test_omits_the_third_step_when_the_error_is_not_recognised(self):
        text = "\n".join(qt_load_failure_message(ImportError("something unrecognised")))

        assert "  3." not in text

    def test_returns_lines_without_trailing_newlines(self):
        """Caller prints line by line; embedded newlines would double-space it."""
        lines = qt_load_failure_message(ImportError(FONT_MISMATCH))

        assert all("\n" not in line for line in lines)
