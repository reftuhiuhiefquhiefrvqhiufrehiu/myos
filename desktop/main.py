import sys
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox, QWidget

from apps.code_studio.app import CodeStudioWindow
from apps.file_manager.app import FileManagerWindow
from apps.file_manager.shortcuts import read_desktop_shortcut
from apps.file_manager.trash import TrashWindow
from apps.image_viewer.app import ImageViewerWindow
from apps.browser.app import BrowserWindow
from apps.music_player.app import MusicPlayerWindow
from apps.pi_tools.app import RaspberryPiToolsWindow
from apps.screenshots.app import ScreenshotController, ScreenshotHotkey, ScreenshotWindow
from apps.settings.app import SettingsWindow
from apps.terminal.app import TerminalWindow
from apps.text_editor.app import TextEditorWindow
from apps.time_manager.app import TimeManagerWindow
from apps.update_manager.app import UpdateManagerWindow
from apps.update_manager.background import BackgroundUpdateWatcher
from notifications import NotificationCenter
from profiles import ProfileChooser, ProfileStore
from shell import DesktopShell
from system_info import AboutMyOSWindow
from taskbar import Taskbar


FILE_APPLICATIONS = {
    **dict.fromkeys({".jpg", ".jpeg", ".png"}, "pictures"),
    **dict.fromkeys({".mp3", ".ogg"}, "music"),
    **dict.fromkeys(
        {
            ".css",
            ".html",
            ".htm",
            ".js",
            ".jsx",
            ".json",
            ".md",
            ".mjs",
            ".py",
            ".svg",
            ".ts",
            ".tsx",
            ".yaml",
            ".yml",
        },
        "code",
    ),
    **dict.fromkeys(
        {
            ".cfg",
            ".conf",
            ".csv",
            ".ini",
            ".log",
            ".sh",
            ".toml",
            ".txt",
            ".xml",
        },
        "editor",
    ),
}


def file_application(path: str | Path) -> str | None:
    return FILE_APPLICATIONS.get(Path(path).suffix.casefold())


def create_settings_window(desktop: DesktopShell) -> SettingsWindow:
    window = SettingsWindow()
    window.wallpaper_changed.connect(desktop.set_wallpaper)
    return window


def create_code_studio_window(
    open_local_url,
    file_path: str | Path | None = None,
) -> CodeStudioWindow:
    window = CodeStudioWindow(file_path)
    window.open_local_url_requested.connect(open_local_url)
    return window


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("neonveil")
    app.setQuitOnLastWindowClosed(False)
    app.aboutToQuit.connect(lambda: QApplication.clipboard().clear())

    profiles = ProfileStore()
    chooser = ProfileChooser(profiles)
    if chooser.exec() != ProfileChooser.DialogCode.Accepted or chooser.profile is None:
        return 0
    current_profile = chooser.profile
    notifications = NotificationCenter()
    update_watcher = BackgroundUpdateWatcher(notifications)
    update_check_timer = QTimer(app)
    update_check_timer.setInterval(6 * 60 * 60 * 1000)
    update_check_timer.timeout.connect(update_watcher.check)
    update_check_timer.start()
    QTimer.singleShot(30_000, update_watcher.check)
    desktop = DesktopShell()
    taskbar = Taskbar()
    screenshot_controller = ScreenshotController()
    screenshot_hotkey = ScreenshotHotkey(screenshot_controller)
    app.installEventFilter(screenshot_hotkey)
    screenshot_controller.screenshot_saved.connect(
        lambda path: notifications.notify(
            "Screenshot gespeichert",
            Path(path).name,
            "Screenshot",
        )
    )
    taskbar.set_profile(current_profile.username)
    notifications.set_theme(desktop.theme)

    def register_window(window: QMainWindow) -> None:
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        window.setProperty("_myos_local_stylesheet", window.styleSheet())
        for child in window.findChildren(QWidget):
            child.setProperty("_myos_original_stylesheet", child.styleSheet())
        window.setStyleSheet(
            DesktopShell.window_stylesheet(desktop.theme)
            + str(window.property("_myos_local_stylesheet") or "")
        )
        apply_child_theme(window, desktop.theme)
        taskbar.add_window(window)
        window.show()

    def apply_child_theme(window: QWidget, theme: str) -> None:
        if theme == "dark":
            replacements = {
                "#f7fafb": "#202a2f",
                "#f7f9fa": "#202a2f",
                "#183840": "#e4ecee",
                "#15313b": "#e4ecee",
                "#123": "#e4ecee",
                "#b8d9dc": "#315550",
                "#a8bec2": "#536a71",
                "#58747c": "#9db5bb",
                "#71858b": "#9db5bb",
                "#14596a": "#69bdb1",
                "#176c67": "#65b9ae",
            }
        else:
            replacements = {}
        for child in window.findChildren(QWidget):
            original = str(child.property("_myos_original_stylesheet") or "")
            if theme == "dark":
                for light, dark in replacements.items():
                    original = original.replace(light, dark)
            child.setStyleSheet(original)

    def show_notification(title: str, message: str, app_name: str = "MyOS") -> None:
        notifications.notify(title, message, app_name)

    def apply_theme(theme: str) -> None:
        desktop.set_theme(theme)
        taskbar.set_theme(theme)
        notifications.set_theme(theme)
        for window in tuple(taskbar._task_windows):
            if isinstance(window, SettingsWindow):
                window.set_appearance(theme, emit=False)
                window.setProperty("_myos_local_stylesheet", window.styleSheet())
            local_styles = str(window.property("_myos_local_stylesheet") or "")
            window.setStyleSheet(DesktopShell.window_stylesheet(theme) + local_styles)
            apply_child_theme(window, theme)

    def logout() -> None:
        desktop.hide()
        taskbar.hide()
        for window in tuple(taskbar._task_windows):
            window.close()
        dialog = ProfileChooser(profiles)
        if dialog.exec() != ProfileChooser.DialogCode.Accepted or dialog.profile is None:
            app.quit()
            return
        nonlocal current_profile
        current_profile = dialog.profile
        taskbar.set_profile(current_profile.username)
        desktop.show()
        taskbar.show()
        show_notification("Angemeldet", f"Willkommen, {current_profile.username}.", "MyOS")

    def open_file(path: str) -> None:
        file_path = Path(path).expanduser().absolute()
        if file_path.is_dir():
            register_window(
                FileManagerWindow(
                    open_file,
                    file_path,
                    open_trash=open_trash_window,
                    desktop_path=desktop.desktop_path,
                )
            )
            return
        if file_path.suffix.casefold() == ".desktop":
            try:
                target, app_id = read_desktop_shortcut(file_path)
            except (OSError, ValueError) as error:
                QMessageBox.warning(
                    desktop, "Verknüpfung kann nicht geöffnet werden", str(error)
                )
                return
            if app_id is not None:
                open_application(app_id)
            elif target is not None:
                open_file(str(target))
            return

        application = file_application(file_path)
        if application == "pictures":
            taskbar.record_recent("pictures")
            register_window(ImageViewerWindow(file_path))
        elif application == "code":
            taskbar.record_recent("code")
            register_window(create_code_studio_window(open_local_browser, file_path))
        elif application == "editor":
            taskbar.record_recent("editor")
            register_window(TextEditorWindow(file_path))
        elif application == "music":
            taskbar.record_recent("music")
            window = MusicPlayerWindow()
            window.add_to_playlist([file_path])
            window.play_index(0)
            register_window(window)
        elif not QDesktopServices.openUrl(file_path.as_uri()):
            QMessageBox.warning(
                desktop,
                "Datei kann nicht geöffnet werden",
                f"Für „{file_path.name}“ ist keine passende Anwendung verfügbar.",
            )

    desktop.file_requested.connect(open_file)

    def open_trash_window() -> None:
        register_window(TrashWindow())

    def open_download_folder(path: str) -> None:
        try:
            folder = Path(path).expanduser().absolute()
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            QMessageBox.critical(
                desktop,
                "Download-Ordner nicht verfügbar",
                f"{path}\n\n{error}",
            )
            return
        register_window(
            FileManagerWindow(
                open_file, folder, desktop_path=desktop.desktop_path
            )
        )
        taskbar.record_recent("downloads")

    def connect_browser_actions(window: BrowserWindow) -> None:
        def open_downloaded_image(path: str) -> None:
            show_notification("Download abgeschlossen", Path(path).name, "Browser")
            open_file(path)

        window.image_downloaded.connect(open_downloaded_image)
        window.open_download_requested.connect(open_file)
        window.open_download_folder_requested.connect(open_download_folder)

    def open_local_browser(address: str) -> None:
        window = BrowserWindow()
        connect_browser_actions(window)
        window.navigate_to_url(address)
        register_window(window)
        taskbar.record_recent("browser")

    def open_application(app_id: str) -> None:
        taskbar.record_recent(app_id)
        if app_id == "notifications":
            register_window(notifications.open_history())
            return
        if app_id == "trash":
            open_trash_window()
            return
        if app_id == "pi-tools":
            window = RaspberryPiToolsWindow()
        elif app_id == "update-manager":
            window = UpdateManagerWindow()
        elif app_id == "about":
            window = AboutMyOSWindow()
        elif app_id == "files":
            window = FileManagerWindow(
                open_file,
                open_trash=open_trash_window,
                desktop_path=desktop.desktop_path,
            )
        elif app_id == "downloads":
            downloads_path = Path.home() / "Downloads"
            try:
                downloads_path.mkdir(parents=True, exist_ok=True)
            except OSError as error:
                QMessageBox.critical(
                    desktop,
                    "Download-Ordner nicht verfügbar",
                    f"{downloads_path}\n\n{error}",
                )
                return
            window = FileManagerWindow(
                open_file,
                downloads_path,
                open_trash=open_trash_window,
                desktop_path=desktop.desktop_path,
            )
        elif app_id == "editor":
            window = TextEditorWindow()
        elif app_id == "pictures":
            window = ImageViewerWindow()
        elif app_id == "clock":
            window = TimeManagerWindow()
            window.timer_finished.connect(
                lambda: show_notification(
                    "Timer abgelaufen",
                    "Dein Timer ist abgelaufen.",
                    "Uhr",
                )
            )
        elif app_id == "music":
            window = MusicPlayerWindow()
        elif app_id == "screenshot":
            window = ScreenshotWindow(screenshot_controller)
        elif app_id == "terminal":
            window = TerminalWindow()
        elif app_id == "code":
            window = create_code_studio_window(open_local_browser)
        elif app_id == "browser":
            window = BrowserWindow()
            connect_browser_actions(window)
        elif app_id == "settings":
            window = create_settings_window(desktop)
            window.appearance_changed.connect(apply_theme)
            window.update_manager_requested.connect(
                lambda: open_application("update-manager")
            )
        else:
            window = QMainWindow()
            window.setWindowTitle(desktop.application_title(app_id))
            window.setMinimumSize(420, 260)
            window.resize(560, 340)
            window.setCentralWidget(desktop.create_application_page(app_id))
        register_window(window)

    def open_update_release(release, *, install: bool) -> None:
        window = UpdateManagerWindow()
        register_window(window)
        if install:
            window.install_release(release)
        else:
            window.show_release(release)

    update_watcher.open_details = (
        lambda release: open_update_release(release, install=False)
    )
    update_watcher.open_install = (
        lambda release: open_update_release(release, install=True)
    )

    desktop.application_requested.connect(open_application)
    taskbar.application_requested.connect(open_application)
    taskbar.logout_requested.connect(logout)
    show_notification("MyOS ist bereit", f"Angemeldet als {current_profile.username}.", "MyOS")
    desktop.show()
    taskbar.show()
    open_application("welcome")

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
