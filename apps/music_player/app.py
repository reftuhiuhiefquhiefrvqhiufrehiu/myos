from pathlib import Path

from PySide6.QtCore import QUrl, Qt, QTimer
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)


AUDIO_FILTER = "Audiodateien (*.mp3 *.ogg *.MP3 *.OGG)"


class MusicPlayerWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Musik")
        self.setMinimumSize(380, 300)
        self.resize(520, 390)
        self.playlist: list[Path] = []
        self.current_index = -1
        self._seeking = False

        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(0.7)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio_output)
        self.player.positionChanged.connect(self._update_position)
        self.player.durationChanged.connect(self._update_duration)
        self.player.mediaStatusChanged.connect(self._media_status_changed)
        self.player.errorOccurred.connect(self._player_error)

        content = QWidget()
        layout = QVBoxLayout(content)
        heading = QLabel("Musikplayer")
        heading.setObjectName("heading")
        self.now_playing = QLabel("Keine Wiedergabe")
        self.now_playing.setWordWrap(True)
        self.items = QListWidget()
        self.items.itemDoubleClicked.connect(self._play_selected)
        self.status = QLabel()
        self.status.setWordWrap(True)

        self.progress = QSlider(Qt.Orientation.Horizontal)
        self.progress.setRange(0, 0)
        self.progress.sliderPressed.connect(lambda: setattr(self, "_seeking", True))
        self.progress.sliderReleased.connect(self._seek)
        self.elapsed = QLabel("00:00 / 00:00")

        controls = QHBoxLayout()
        self.add_button = QPushButton("Titel hinzufügen…")
        self.add_button.clicked.connect(self.add_files)
        self.previous_button = QPushButton("Zurück")
        self.previous_button.clicked.connect(self.previous)
        self.play_button = QPushButton("Wiedergabe")
        self.play_button.clicked.connect(self.toggle_playback)
        self.next_button = QPushButton("Weiter")
        self.next_button.clicked.connect(self.next)
        controls.addWidget(self.add_button)
        controls.addWidget(self.previous_button)
        controls.addWidget(self.play_button)
        controls.addWidget(self.next_button)

        volume_row = QHBoxLayout()
        volume_row.addWidget(QLabel("Lautstärke"))
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(70)
        self.volume_slider.valueChanged.connect(
            lambda value: self.audio_output.setVolume(value / 100)
        )
        volume_row.addWidget(self.volume_slider, 1)

        layout.addWidget(heading)
        layout.addWidget(self.now_playing)
        layout.addWidget(self.items, 1)
        layout.addWidget(self.progress)
        layout.addWidget(self.elapsed, 0, Qt.AlignmentFlag.AlignRight)
        layout.addLayout(controls)
        layout.addLayout(volume_row)
        layout.addWidget(self.status)
        self.setCentralWidget(content)

        self.position_timer = QTimer(self)
        self.position_timer.timeout.connect(
            lambda: self._update_position(self.player.position())
        )
        self.position_timer.start(500)

    def add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Musik zur Playlist hinzufügen", str(Path.home()), AUDIO_FILTER
        )
        self.add_to_playlist([Path(path) for path in paths])

    def add_to_playlist(self, paths: list[Path]) -> None:
        for path in paths:
            if path.suffix.casefold() not in {".mp3", ".ogg"}:
                continue
            self.playlist.append(path)
            self.items.addItem(QListWidgetItem(path.name))
        if self.current_index < 0 and self.playlist:
            self.items.setCurrentRow(0)

    def _play_selected(self, item: QListWidgetItem) -> None:
        index = self.items.row(item)
        if 0 <= index < len(self.playlist):
            self.play_index(index)

    def play_index(self, index: int) -> None:
        if not 0 <= index < len(self.playlist):
            return
        self.current_index = index
        path = self.playlist[index]
        self.items.setCurrentRow(index)
        self.now_playing.setText(path.name)
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.player.play()
        self.play_button.setText("Pause")

    def toggle_playback(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.play_button.setText("Wiedergabe")
        elif self.current_index >= 0:
            self.player.play()
            self.play_button.setText("Pause")
        elif self.playlist:
            self.play_index(0)
        else:
            self.status.setText("Füge zuerst MP3- oder OGG-Dateien zur Playlist hinzu.")

    def previous(self) -> None:
        if self.playlist:
            self.play_index((self.current_index - 1) % len(self.playlist))

    def next(self) -> None:
        if self.playlist:
            self.play_index((self.current_index + 1) % len(self.playlist))

    def _seek(self) -> None:
        self._seeking = False
        self.player.setPosition(self.progress.value())

    def _update_position(self, position: int) -> None:
        if not self._seeking:
            self.progress.setValue(position)
        self.elapsed.setText(
            f"{self._format_time(position)} / {self._format_time(self.player.duration())}"
        )

    def _update_duration(self, duration: int) -> None:
        self.progress.setRange(0, max(0, duration))
        self._update_position(self.player.position())

    def _media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia and self.playlist:
            self.next()

    def _player_error(self, _error: QMediaPlayer.Error, message: str) -> None:
        self.status.setText(
            f"Audiodatei kann nicht abgespielt werden: {message or _error}"
        )
        self.play_button.setText("Wiedergabe")

    @staticmethod
    def _format_time(milliseconds: int) -> str:
        seconds = max(0, milliseconds // 1000)
        minutes, seconds = divmod(seconds, 60)
        return f"{minutes:02}:{seconds:02}"

    def closeEvent(self, event) -> None:
        self.player.stop()
        super().closeEvent(event)
