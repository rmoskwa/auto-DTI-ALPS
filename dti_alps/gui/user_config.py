"""
User configuration persistence for DTI-ALPS GUI.

Stores user preferences like last used directories for file dialogs.
Configuration is saved to ~/.dti-alps/user_config.json
"""

import json
from pathlib import Path


class UserConfig:
    """
    Manages persistent user configuration for the DTI-ALPS GUI.

    Stores and retrieves user preferences such as last used directories
    for various file/folder browse dialogs.
    """

    # Default config directory in user's home
    CONFIG_DIR = Path.home() / ".dti-alps"
    CONFIG_FILE = CONFIG_DIR / "user_config.json"

    # Keys for different path entry fields
    KEY_SUBJECT_FOLDER = "last_subject_folder"
    KEY_OUTPUT_DIR = "last_output_dir"
    KEY_SYNB0_OUTPUT_DIR = "last_synb0_output_dir"
    KEY_CLI_FILE = "last_cli_file_dir"
    KEY_CLI_DIR = "last_cli_dir"
    KEY_CLI_SAVE = "last_cli_save_dir"
    KEY_CSV_EXPORT = "last_csv_export_dir"
    KEY_VIEWER_FOLDER = "last_viewer_folder"

    def __init__(self):
        """Initialize user config, loading from file if it exists."""
        self._config: dict = {}
        self._load()

    def _load(self) -> None:
        """Load configuration from JSON file."""
        if self.CONFIG_FILE.exists():
            try:
                with open(self.CONFIG_FILE, encoding="utf-8") as f:
                    self._config = json.load(f)
            except (json.JSONDecodeError, OSError):
                # If file is corrupted or unreadable, start fresh
                self._config = {}

    def _save(self) -> None:
        """Save configuration to JSON file."""
        try:
            self.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            with open(self.CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self._config, f, indent=2)
        except OSError:
            # Silently fail if we can't save (e.g., permissions issue)
            pass

    def get(self, key: str, default: str | None = None) -> str | None:
        """
        Get a configuration value.

        Parameters
        ----------
        key : str
            Configuration key
        default : str, optional
            Default value if key doesn't exist

        Returns
        -------
        str or None
            The stored value or default
        """
        return self._config.get(key, default)

    def set(self, key: str, value: str) -> None:
        """
        Set a configuration value and save to file.

        Parameters
        ----------
        key : str
            Configuration key
        value : str
            Value to store
        """
        self._config[key] = value
        self._save()

    def get_initial_dir(self, key: str) -> str | None:
        """
        Get the initial directory for a file dialog.

        Returns None if the stored path doesn't exist (directory was deleted).

        Parameters
        ----------
        key : str
            Configuration key for the path type

        Returns
        -------
        str or None
            The directory path if it exists, None otherwise
        """
        path = self.get(key)
        if path and Path(path).exists():
            return path
        return None

    def set_from_path(self, key: str, filepath: str) -> None:
        """
        Set the initial directory from a selected file or folder path.

        For files, stores the parent directory.
        For directories, stores the directory itself.

        Parameters
        ----------
        key : str
            Configuration key for the path type
        filepath : str
            The selected file or folder path
        """
        path = Path(filepath)
        if path.is_file():
            # Store parent directory for files
            self.set(key, str(path.parent))
        elif path.is_dir():
            # Store directory itself
            self.set(key, str(path))
        elif path.parent.exists():
            # Path doesn't exist yet (save dialog), store parent
            self.set(key, str(path.parent))


# Singleton instance for the application
_user_config: UserConfig | None = None


def get_user_config() -> UserConfig:
    """
    Get the singleton UserConfig instance.

    Returns
    -------
    UserConfig
        The shared user configuration instance
    """
    global _user_config
    if _user_config is None:
        _user_config = UserConfig()
    return _user_config
