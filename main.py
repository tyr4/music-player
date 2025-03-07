from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
from ctypes import cast, POINTER
from comtypes import CLSCTX_ALL
import pythoncom

from PyQt6.QtWidgets import QApplication, QWidget, QLabel, QSlider, QPushButton, QScrollArea, QVBoxLayout, QHBoxLayout, QLineEdit, QFrame
from PyQt6.QtGui import QPixmap, QMouseEvent, QFont, QFontMetrics, QIcon, QColor, QPainter, QBrush, QPen
from PyQt6.QtCore import Qt, QTimer, QRect, QAbstractNativeEventFilter, QPropertyAnimation, pyqtSignal, QEvent

from winsdk.windows.media.playback import MediaPlayer
from winsdk.windows.media.core import MediaSource
from winsdk.windows.storage import StorageFile
from winsdk.windows.storage.streams import RandomAccessStreamReference

from pytubefix import Playlist, YouTube
from pytubefix.cli import on_progress
import urllib.request as request

from PIL import Image, ImageQt

from copy import deepcopy
import asyncio
from datetime import timedelta
import os
import random
import json
import re
import time

# to do whatever stuff
# 1. actual song queue
# 2. fix x button for the download thing
# 3. fix text going out of bounds for the download thing
# 4. slider ui
# 5. json/code cleanup
# 6. maybe add a background

with open('config.json', 'r') as f:
    config = json.load(f)
    prev = deepcopy(config) # to avoid useless file openings

class DimOverlay(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 150))  # Semi-transparent black

class PopupInputDialog(QWidget):
    submitted = pyqtSignal(str)
    canceled = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.init_ui()
        self.setFixedSize(config['width'] - 200, config['height'] - 200)

    def init_ui(self):
        self.container = QFrame(self)
        self.container.setStyleSheet("""
             QFrame {
                 background-color: #404040;
                 border-radius: 10px;
                 border: none; /* Remove any default border */
             }
         """)
        self.container.setContentsMargins(0, 0, 0, 0)

        self.layout = QVBoxLayout(self.container)
        self.layout.setSizeConstraint(QVBoxLayout.SizeConstraint.SetFixedSize)

        close_btn = QPushButton("✕")
        close_btn.setStyleSheet("""
             QPushButton {
                 color: white;
                 font-size: 18px;
                 border: none;
                 padding: 0 8px;
             }
             QPushButton:hover { background-color: #505050; }
         """)
        close_btn.clicked.connect(self.cancel)
        close_btn.setFixedSize(30, 30)

        enter_text_button = QPushButton("Enter a YouTube link/playlist")
        enter_text_button.setStyleSheet("""
             QPushButton {
                 background-color: transparent;
                 color: white;
                 text-align: left;
                 border: 1px solid transparent;
                 border-radius: 5px;
                 padding: 0px;
                 font-size: 19px;
                 font-family: Comic Sans MS;
             }
         """)
        enter_text_button.setFixedWidth(300)

        header_layout = QHBoxLayout()
        header_layout.addWidget(enter_text_button)
        header_layout.addStretch()
        header_layout.setSpacing(config['width'] - 600)
        header_layout.addWidget(close_btn)

        self.popup_input = QLineEdit()
        self.popup_input.returnPressed.connect(self.accept)
        self.popup_input.setStyleSheet("""
             QLineEdit {
                 background-color: #505050;
                 color: white;
                 border: 0px solid #505050;
                 border-radius: 6px;
                 padding: 8px;
                 font-size: 19px;
                 font-family: Comic Sans MS;
             }
         """)

        self.layout.addLayout(header_layout)
        self.layout.addWidget(self.popup_input)

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.main_layout.addWidget(self.container)

    def add_button(self, text):
        new_button = QPushButton(text)
        new_button.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: white;
                border-radius: 5px;
                text-align: left;
                padding: 8px;
                font-size: 25px;
                font-family: Comic Sans MS;
            }
        """)
        self.layout.addWidget(new_button)
        self.layout.update()
        self.main_layout.update()
        return new_button

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        brush = QBrush(QColor(64, 64, 64))
        pen = QPen(Qt.PenStyle.NoPen)
        painter.setBrush(brush)
        painter.setPen(pen)
        painter.drawRoundedRect(self.rect(), 10, 10)

    def accept(self):
        if text := self.popup_input.text():
            self.submitted.emit(text)

    def cancel(self):
        self.canceled.emit()
        self.close()

    def show_centered(self, parent):
        parent_center = parent.mapToGlobal(parent.rect().center())
        self.move(parent_center.x() - self.width() // 2,
                  parent_center.y() - self.height() // 2)
        self.show()

    def showEvent(self, event):
        self.popup_input.setFocus()
        self.installEventFilter(self)
        super().showEvent(event)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.MouseButtonPress:
            if not self.geometry().contains(event.globalPosition().toPoint()):
                self.cancel()
                return True
        return super().eventFilter(obj, event)

# this exists because comtypes likes to garbage collect itself mid program and cause it to crash...
class AudioController:
    def __init__(self):
        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        self.volume = cast(interface, POINTER(IAudioEndpointVolume))  # Store reference

    def change_system_volume_util(self, value, current_file):
        # stupid exception case to avoid adding the "true" key to dict
        if current_file == 1:
            return

        config['modified_volumes'][current_file] = round(config['base_volume'] * value) / 100

        self.volume.SetMasterVolumeLevelScalar(config['modified_volumes'][current_file], None)

class SmoothScrollArea(QScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)

        self.scroll_timer = QTimer(self)
        self.scroll_timer.timeout.connect(self.smooth_scroll)
        self.scroll_delta = 0

        self.reset_timer = QTimer(self)
        self.reset_timer.setSingleShot(True)
        self.reset_timer.timeout.connect(self.reset_scroll_delta)

    def wheelEvent(self, event):
        delta = event.angleDelta().y()

        if delta != 0:
            self.scroll_delta += delta
            if not self.scroll_timer.isActive():
                self.scroll_timer.start(10)

            self.reset_timer.start(500)

    def smooth_scroll(self):
        if abs(self.scroll_delta) > 1:
            current_value = self.verticalScrollBar().value()
            step = max(abs(self.scroll_delta) // 10, 1)

            if self.scroll_delta > 0:
                new_value = max(self.verticalScrollBar().minimum(), current_value - step)
                self.scroll_delta -= step
            else:
                new_value = min(self.verticalScrollBar().maximum(), current_value + step)
                self.scroll_delta += step

            self.verticalScrollBar().setValue(new_value)

        else:
            self.scroll_timer.stop()

    def reset_scroll_delta(self):
        self.scroll_delta = 0

class ClickableImage(QLabel):
    def __init__(self, image_path, click_callback, parent=None, size=(128, 128)):
        super().__init__(parent)
        self.resize_pix = size
        self.normal_image_path = image_path
        self.hover_image_path = image_path.replace(".png", "_hover.png")

        self.normal_image = Utils.resize_image(self.normal_image_path, self.resize_pix)
        self.hover_image = Utils.resize_image(self.hover_image_path, self.resize_pix)

        self.setPixmap(self.normal_image)
        self.setFixedSize(self.pixmap().size())
        self.click_callback = click_callback

        self.is_hovered = False

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            if self.property('song_path'):
                self.click_callback(self.property('song_path'))
            else:
                self.click_callback()

    def enterEvent(self, event):
        self.is_hovered = True
        self.setPixmap(self.hover_image)

    def leaveEvent(self, event):
        self.is_hovered = False
        self.setPixmap(self.normal_image)

    def set_image(self, image_path):
        self.normal_image_path = image_path
        self.hover_image_path = image_path.replace(".png", "_hover.png")

        self.normal_image = Utils.resize_image(self.normal_image_path, self.resize_pix)
        self.hover_image = Utils.resize_image(self.hover_image_path, self.resize_pix)

        self.setPixmap(self.hover_image if self.is_hovered else self.normal_image)

class MusicPlayer(QWidget):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Music Player")
        self.setWindowIcon(QIcon(r"D:\Music Player\Music Player\app_icon_ico.ico"))
        self.setGeometry(400, 50, config['width'], config['height'])

        self.audio_controller = AudioController()
        self.player = MediaPlayer()
        self.button_manager = self.player.system_media_transport_controls
        self.button_manager.add_button_pressed(self.on_button_pressed)

        self.setStyleSheet("""
            QLabel {
                color: white;
            }
            QWidget {
                background-color: #2C2F33; /* Dark gray */
            }
        """)

        # background which acts as a text label cause yea
        self.background = QLabel(self)
        self.background.setGeometry(0, 0, config['width'], config['height'])
        # self.background.setPixmap(QPixmap.fromImage(QImage("assets/smoothness-pastel-colors-on-backgro.png")))
        self.background.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.background.setScaledContents(True)

        # debugging stuff
        self.coord_label = QLabel("Mouse Coordinates: (0, 0)", self)
        self.coord_label.setFont(QFont('Comic Sans MS', 12))
        self.coord_label.setGeometry(10, 10, 300, 30)
        self.coord_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.coord_label.setStyleSheet("background-color: transparent;")
        self.setMouseTracking(True)

        # "global" variables for tracking stuff
        self.currently_playing_file: int | str = 0
        self.is_dragging_progress_bar = False
        self.is_dragging_duration_bar = False
        self.duration_slider_mode = 'right'
        self.prev_ms = 0
        self.manual_pause = True
        self.buttons = []
        self.overlay_download_button = None
        self.overlay_button = None
        self.overlay_image = None

        # song buttons
        self.button_container = QWidget(self)
        self.button_layout = QVBoxLayout(self.button_container)
        self.button_container.setStyleSheet("background-color: transparent;")

        # scroll area for songs
        self.scroll_area = SmoothScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setGeometry(180, 80, config['width'] - 180 * 2, config['height'] - 200)
        self.scroll_area.verticalScrollBar().valueChanged.connect(self.check_top_slider_widget)
        self.scroll_area.viewport().setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.scroll_area.setStyleSheet("""
            QScrollArea {
                background: transparent;
            }
            QScrollArea::viewport {
                background: transparent;
            }
        """)

        # the letter at the top of the list for easier searching through songs
        self.song_key_label = QPushButton("クロリンデ",self)  # clorinde ♥
        self.song_key_label.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: white; /* Text color */
                text-align: left;
                padding-left: 10px;
                font-family: 'Comic Sans MS';
                font-size: 16pt;
                border-radius: 10px;
            }
        """)
        self.song_key_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.song_key_label.setGeometry(185, 29, config['width'] - 180 * 2 - 35, 50)

        self.add_songs_to_layout(match=" ")

        # play/pause button
        self.play_button = ClickableImage("assets/play_button.png", self.play_pause_button, self, size=(80, 80))
        self.play_button.move(config['width'] // 2 - 128 // 2, int(config['height'] - 155))
        self.play_button.setStyleSheet("background-color: transparent;")

        # next button
        self.next_button_widget = ClickableImage("assets/next_button.png", self.next_button, self, size=(40, 40))
        self.next_button_widget.move(int(config['width'] // 2 + 128 / 1.5), int(config['height'] - 130))
        self.next_button_widget.setStyleSheet("background-color: transparent;")

        # previous button
        self.previous_button_widget = ClickableImage("assets/previous_button.png", self.previous_button, self, size=(40, 40))
        self.previous_button_widget.move(int(config['width'] // 2 - 128 * 1.285), int(config['height'] - 130))
        self.previous_button_widget.setStyleSheet("background-color: transparent;")

        # volume slider
        self.volume_slider = QSlider(Qt.Orientation.Horizontal, self)
        self.volume_slider.setGeometry(101, config['height'] - 120, 200, 50)
        self.volume_slider.setMinimum(0)
        self.volume_slider.setMaximum(150)
        self.volume_slider.setValue(100)

        self.volume_slider.valueChanged.connect(self.change_system_volume)
        self.volume_slider.setStyleSheet(f"""
                    QSlider {{
                        background: transparent;
                    }}
                    QSlider::groove:horizontal {{
                        border: 1px solid #bbb;
                        background: #ddd;
                        height: 8px;
                        border-radius: 4px;
                    }}

                    QSlider::handle:horizontal {{
                        background: url('assets/solar_flare.png');
                        border: none;
                        width: 50px;
                        height: 200px;
                        margin: -13px -6px -20px -6px;
                        border-radius: 20px;
                        background-size: contain;
                        background-repeat: no-repeat;
                        background-position: center;
                    }}

                    QSlider::sub-page:horizontal {{
                        background: url('assets/volume_background.png');
                        border-radius: 4px;
                    }}

                    QSlider::add-page:horizontal {{
                        background: #ddd;
                        border-radius: 4px;
                    }}
                """)

        # volume number
        self.volume_number_text = QLabel(f"{config['base_volume']}", self)
        self.volume_number_text.setGeometry(310, config['height'] - 131, 100, 50)
        self.volume_number_text.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.volume_number_text.setFont(QFont('Comic Sans MS', 14))
        self.volume_number_text.setStyleSheet("background-color: transparent;")

        # song progress bar
        self.progress_bar_slider = QSlider(Qt.Orientation.Horizontal, self)
        self.progress_bar_slider.setGeometry(180, config['height'] - 233, config['width'] - 180 * 2, 100)
        self.progress_bar_slider.setMinimum(0)
        self.progress_bar_slider.setMaximum(100000)
        self.progress_bar_slider.setValue(0)
        self.progress_bar_slider.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self.progress_bar_slider.setStyleSheet("""
            QSlider {
            background: transparent;
            }
            QSlider::groove:horizontal {
                border: 1px solid #bbb;
                background: #ddd;
                height: 8px;
                border-radius: 4px;
            }

            QSlider::handle:horizontal {
                background: url('assets/planet_1.png');
                border: none;  /* Remove border so it's not squared off */
                width: 100px;  /* Increase size */
                height: 100px; /* Increase size */
                margin: -40px -30px -40px -30px;  /* Adjust positioning */
                border-radius: 24px;  /* Force a circle */
                background-size: contain;  /* Ensure full image is visible */
                background-repeat: no-repeat;
                background-position: center;
            }

            QSlider::handle:horizontal:hover {
                background: url('assets/play_button_small.png');
                border: none;
            }

            QSlider::sub-page:horizontal {
                background: #ff9800;
                border-radius: 4px;
            }

            QSlider::add-page:horizontal {
                background: #ddd;
                border-radius: 4px;
            }
        """)

        # global 60s timer
        self.timer_60s = QTimer(self)
        self.timer_60s.timeout.connect(self.global_timer_task_60s)
        self.timer_60s.start(60 * 1000)  # 10000ms = 60s

        # global 0.3s timer
        self.timer_03s = QTimer(self)
        self.timer_03s.timeout.connect(self.global_timer_task_03s)
        self.timer_03s.start(300)  # 300ms = 0.3s

        # display the current song title
        self.song_title_text = QLabel("", self)
        self.song_title_text.setGeometry(100, config['height'] - 110, config['width'], 50)
        self.song_title_text.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.song_title_text.setFont(QFont('Comic Sans MS', 18))
        self.song_title_text.setStyleSheet("background-color: transparent;")

        # current song time
        self.current_song_time_text = QLabel("00:00", self)
        self.current_song_time_text.setGeometry(110, config['height'] - 211, 100, 50)
        self.current_song_time_text.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.current_song_time_text.setFont(QFont('Comic Sans MS', 14))
        self.current_song_time_text.setStyleSheet("background-color: transparent;")

        # total song time
        self.total_song_time_text = QLabel("00:00", self)
        self.total_song_time_text.setGeometry(config['width'] - 180 + 15, config['height'] - 211, 100, 50)
        self.total_song_time_text.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.total_song_time_text.setFont(QFont('Comic Sans MS', 14))
        self.total_song_time_text.setStyleSheet("background-color: transparent;")

        # sliders for changing the duration of the song, where it should start/stop. max is midway for each slider
        self.song_start_slider = QSlider(Qt.Orientation.Horizontal, self)
        self.song_start_slider.setGeometry(180, config['height'] - 160, config['width'] // 2 - 180, 100)
        self.song_start_slider.setMinimum(0)
        self.song_start_slider.setMaximum(100000)
        self.song_start_slider.setValue(0)
        self.song_start_slider.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.song_start_slider.setStyleSheet(f"""
                    QSlider {{
                    background: transparent;
                    }}
                    QSlider::groove:horizontal {{
                        border: 1px solid #bbb;
                        background: #ddd;
                        height: 8px;
                        border-radius: 4px;
                    }}

                    QSlider::handle:horizontal {{
                        background: url('assets/black_hole.png');
                        border: none;
                        width: 100px;
                        height: 100px;
                        margin: -80px -37px -80px -33px;
                        border-radius: 24px;
                        background-size: contain;
                        background-repeat: no-repeat;
                        background-position: center;
                    }}

                    QSlider::sub-page:horizontal {{
                        background: url('assets/cosmic_background_flipped.png');
                        border-radius: 4px;
                    }}

                    QSlider::add-page:horizontal {{
                        background: #ddd;
                        border-radius: 4px;
                    }}
                """)

        self.song_end_slider = QSlider(Qt.Orientation.Horizontal, self)
        self.song_end_slider.setGeometry(config['width'] // 2, config['height'] - 160, config['width'] // 2 - 180, 100)
        self.song_end_slider.setMinimum(50000)  # halfway point
        self.song_end_slider.setMaximum(100000)
        self.song_end_slider.setValue(100000)
        self.song_end_slider.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.song_end_slider.setStyleSheet(f"""
                    QSlider {{
                    background: transparent;
                    }}
                    QSlider::groove:horizontal {{
                        border: 1px solid #bbb;
                        background: #ddd;
                        height: 8px;
                        border-radius: 4px;
                    }}

                    QSlider::handle:horizontal {{
                        background: url('assets/black_hole.png');
                        border: none;
                        width: 100px;
                        height: 100px;
                        margin: -80px -31px -80px -30px;
                        border-radius: 24px;
                        background-size: contain;
                        background-repeat: no-repeat;
                        background-position: center;
                    }}

                    QSlider::sub-page:horizontal {{
                        background: #ddd;
                        border-radius: 4px;
                    }}

                    QSlider::add-page:horizontal {{
                        background: url('assets/cosmic_background_flipped.png');
                        border-radius: 4px;
                    }}
                """)

        # search bar for songs
        self.search_song = QLineEdit(self)
        self.search_song.setGeometry(config['width'] - 180 * 2 - 120, 30, 200, 40)
        self.search_song.setPlaceholderText("Search a song here...")
        self.search_song.textChanged.connect(self.on_search_bar_text_changed)
        self.search_song.setStyleSheet("""
              QLineEdit {
                  color: white; /* Text color */
                  text-align: right;
                  font-family: 'Comic Sans MS';
                  font-size: 14pt;
                  border: 2px solid #ffffff;
                  border-radius: 6px;         /* Rounded corners */
              }
              QLineEdit:focus {
                border: 2px solid #cccccc;
                background: transparent;
            }
          """)

        # download song button
        self.download_song = QPushButton("Download a song", self)
        self.download_song.setGeometry(config['width'] - 180 * 2 - 630, 100, 200, 40)
        self.download_song.clicked.connect(self.show_popup)
        self.download_song.setStyleSheet("""
             QPushButton {
                 background-color: rgba(0, 0, 0, 0);
                 text-align: center;
                 padding-left: 5px;
                 font-family: 'Comic Sans MS';
                 font-size: 14pt;
                 border: 2px solid #ffffff;
                 border-radius: 5px;
                 color: white;
             }
             QPushButton:hover {
                 background-color: rgba(0, 0, 0, 50);
             }
             QPushButton:pressed {
                 background-color: rgba(0, 0, 0, 75);
             }
         """)

        self.overlay = DimOverlay(self)
        self.overlay.resize(self.size())
        self.overlay.hide()

        # set the mouse coords label on top
        # self.coord_label.raise_()

    def update_overlay_button(self, rez):
        if not self.overlay_image:
            self.overlay_image = self.popup.add_button("")
            self.overlay_image.setFixedSize(config['width'] - 300, config['height'] - 500)
        self.overlay_button.setText(rez)

        if rez.startswith("Successfully"):
            rez = rez[24:-1]
        elif rez.startswith("Couldn't"):
            return
        else:
            rez = rez[:-23]

        temp = Utils.resize_image(f"thumbnails/{rez}.jpg", size=(int(1280 / 1.7), int(720 / 1.7)))
        temp.save("thumbnails/temp_image.jpg")
        self.overlay_image.setStyleSheet(f"""
            QPushButton {{
                background: url('thumbnails/temp_image.jpg');
                background-repeat: no-repeat;
                background-position: center;
                border: none;
            }}
        """)
        self.add_songs_to_layout(match="")

    def show_popup(self):
        self.overlay.raise_()
        self.overlay.show()

        self.popup = PopupInputDialog(self)
        self.popup.submitted.connect(self.handle_input)
        self.popup.canceled.connect(self.close_popup)
        self.popup.show_centered(self)

    def close_popup(self):
        self.overlay.hide()
        self.overlay_download_button = None
        self.overlay_button = None
        self.overlay_image = None

    def handle_input(self, text):
        if self.overlay_button:
            self.overlay_button.setText("")
        self.overlay.show()
        if self.overlay_image:
            self.overlay_image.setStyleSheet("background: transparent;")

        # force ui updates, yes twice cause once doesnt work lol
        QApplication.processEvents()
        QApplication.processEvents()

        if "/playlist" in text:
            rez = Utils.download_playlist(self, text)
        else:
            rez = Utils.download_link(self, text)

        if not self.overlay_button:
            if rez == 0:
                self.overlay_button = self.popup.add_button(rez)
                self.update_overlay_button(rez)
            elif rez == -1:
                self.overlay_button = self.popup.add_button("This link is invalid!")
            else:
                self.overlay_button = self.popup.add_button(rez)
                self.update_overlay_button(rez)
        else:
            if rez == 0:
                self.update_overlay_button(rez)
            elif rez == -1:
                self.overlay_button.setText("This link is invalid!")
            else:
                self.update_overlay_button(rez)

    def on_search_bar_text_changed(self, text):
        self.add_songs_to_layout(match=text)

    def add_songs_to_layout(self, match: str):
        self.button_container = QWidget(self)
        self.button_layout = QVBoxLayout(self.button_container)
        self.button_layout.setSizeConstraint(QVBoxLayout.SizeConstraint.SetFixedSize)
        self.buttons = []
        play_buttons = []
        self.button_container.setStyleSheet("background-color: transparent;")

        last_letter = None
        songs = Utils.sort_songs(match=match)

        for song in songs:
            song_layout = QHBoxLayout()

            play_button = ClickableImage("assets/play_button_small.png", self.slider_play_a_song, self, size=(30, 31))
            play_button.setProperty('song_path', config['music_path'] + song)
            play_buttons.append(play_button)

            if re.search(r"[a-zA-Z]", song[0]):
                if last_letter != song[0].lower():
                    empty_layout = QHBoxLayout()

                    separator = QPushButton(song[0].upper(), self)
                    # separator = QPushButton(song[0].upper(), self)
                    separator.setStyleSheet("""
                        QPushButton {
                            background-color: transparent;
                            color: white; /* Text color */
                            text-align: left;
                            padding-left: 10px;
                            font-family: 'Comic Sans MS';
                            font-size: 18pt;
                            border-radius: 10px;
                        }
                    """)
                    separator.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

                    empty_layout.addWidget(separator)
                    self.button_layout.addLayout(empty_layout)

                    last_letter = song[0].lower()

            song_label = QPushButton(song[:-4], self)
            song_label.setFixedHeight(40)
            song_label.setStyleSheet("""
                QPushButton {
                    background-color: rgba(0, 0, 0, 0);
                    text-align: left;
                    padding-left: 5px;
                    font-family: 'Comic Sans MS';
                    font-size: 10pt;
                    border-radius: 5px;
                    color: white;
                    padding: 10px;
                }
                QPushButton:hover {
                    background-color: rgba(0, 0, 0, 50);
                }
                QPushButton:pressed {
                    background-color: rgba(0, 0, 0, 75);
                }
            """)

            # add to horizontal layout
            song_layout.addWidget(play_button)
            song_layout.addWidget(song_label)

            # add the layout to the button container
            self.buttons.append(song_label)
            self.button_layout.addLayout(song_layout)

        self.scroll_area.setWidget(self.button_container)
        self.check_top_slider_widget(0)

    def check_top_slider_widget(self, value):
        for qhbox in self.button_layout.children():
            text_vertical_position = qhbox.itemAt(0).widget().pos().y()
            text_value = qhbox.itemAt(0).widget().text()
            try:
                text_value_song = qhbox.itemAt(1).widget().text()
            except:
                text_value_song = 0

            if value >= text_vertical_position - 10 and text_value:
                self.song_key_label.setText(text_value)
            elif type(text_value_song) == str and not re.search(r"[a-zA-Z]", text_value_song[0]):
                self.song_key_label.setText("クロリンデ")  # clorinde ♥

    def slider_play_a_song(self, custom_song=None):
        self.currently_playing_file = 1
        self.change_song_time(0)
        self.update_song_progress_bar_stylesheet(custom_percentage=1)
        self.play_a_song(custom_song)

    def update_song_progress_bar_stylesheet(self, custom_percentage=-1):
        total = int(self.player.playback_session.natural_duration.total_seconds()) * 1000
        remaining = int(self.player.playback_session.position.total_seconds()) * 1000
        state = self.player.playback_session.playback_state

        if custom_percentage == -1:
            custom_percentage = int((remaining / max(1, total)) * 100)

            # funky logic to set the correct image
            if custom_percentage == 100 and self.progress_bar_slider.value() < 10000:
                custom_percentage = 1
            elif custom_percentage == 100 or (state == 1 and self.progress_bar_slider.value() > 10000):
                custom_percentage = 98
            elif state == 1 and self.progress_bar_slider.value() < 10000:
                custom_percentage = 1

        path = f"assets/planet_{(max(0, custom_percentage - 1)) // 11 + 1}.png"
        self.progress_bar_slider.setStyleSheet(f"""
                    QSlider {{
                    background: transparent;
                    }}
                    QSlider::groove:horizontal {{
                        border: 1px solid #bbb;
                        background: #ddd;
                        height: 8px;
                        border-radius: 4px;
                    }}

                    QSlider::handle:horizontal {{
                        background: url('{path}');
                        border: none;
                        width: 100px;
                        height: 100px;
                        margin: -40px -30px -40px -30px;
                        border-radius: 24px;
                        background-size: contain;
                        background-repeat: no-repeat;
                        background-position: center;
                    }}

                    QSlider::sub-page:horizontal {{
                        background: url('assets/cosmic_background.png');
                        border-radius: 4px;
                    }}

                    QSlider::add-page:horizontal {{
                        background: #ddd;
                        border-radius: 4px;
                    }}
                """)

    def song_progress_bar_update(self, event):
        pos = event.pos()
        x, y = pos.x(), pos.y()
        width, height = self.width(), self.height()

        if 180 <= x <= width - 180 and (height - 194 <= y <= height - 178 or self.is_dragging_progress_bar is True):
            percentage = (x - 180) / self.progress_bar_slider.width()
            self.change_song_time(0, percentage)
            self.update_song_progress_bar_stylesheet()

    def song_duration_limit_update(self, event):
        pos = event.pos()
        x, y = pos.x(), pos.y()
        width, height = self.width(), self.height()

        # left slider
        if 180 <= x <= width // 2 and (height - 154 <= y <= height - 140 or self.is_dragging_duration_bar is True):
            if self.duration_slider_mode == 'left':
                percentage = 1 + (x - self.width() // 2) / self.song_start_slider.width()
                self.song_start_slider.setValue(round(percentage * 100000))
                try:
                    config['modified_times'][self.currently_playing_file][0] = round(percentage, 2)
                except:
                    config['modified_times'][self.currently_playing_file] = [round(percentage, 2), 1.0]
                self.func_pause_music()
                self.change_song_time(0, percentage / 2)

        # right slider
        elif width // 2 + 1 <= x <= width - 180 and (height - 154 <= y <= height - 140 or self.is_dragging_duration_bar is True):
            if self.duration_slider_mode == 'right':
                # i hate this, it just sets the slider to the correct value
                # while the percentage goes from 0.5 to 1.0
                percentage = (((x - 180) / self.song_end_slider.width()) / 2) - 0.5
                self.song_end_slider.setValue(round((percentage + 0.5) * 100000))
                try:
                    config['modified_times'][self.currently_playing_file][1] = round(percentage + 0.5, 2)
                except:
                    config['modified_times'][self.currently_playing_file] = [0.0, round(percentage + 0.5, 2)]

    def func_pause_music(self):
        state = self.player.playback_session.playback_state
        if state >= 3:
            self.player.pause()
            self.play_button.set_image("assets/play_button.png")

    def func_unpause_music(self):
        state = self.player.playback_session.playback_state
        if state <= 4:
            self.player.play()
            self.play_button.set_image("assets/pause_button.png")

    def fix_overlapping_text(self):
        while Utils.is_overlapping_text(self.previous_button_widget, self.song_title_text) and self.song_title_text.text() != "...":
            self.song_title_text.setText(self.song_title_text.text()[:-4] + "...")
        else:
            if type(self.currently_playing_file) == str:
                self.song_title_text.setText(self.currently_playing_file[len(config['music_path']):-4])
                while Utils.is_overlapping_text(self.previous_button_widget, self.song_title_text) and self.song_title_text.text() != "...":
                    self.song_title_text.setText(self.song_title_text.text()[:-4] + "...")

    def mousePressEvent(self, event: QMouseEvent):
        state = self.player.playback_session.playback_state
        if state != 0:
            pos = event.pos()
            x, y = pos.x(), pos.y()
            width, height = self.width(), self.height()

            if 180 <= x <= width - 180 and height - 194 <= y <= height - 178:
                if event.button() == Qt.MouseButton.LeftButton:
                    self.song_progress_bar_update(event)
                    self.is_dragging_progress_bar = True

            # left duration slider
            elif 180 <= x <= width // 2 and height - 154 <= y <= height - 140:
                self.song_duration_limit_update(event)
                self.duration_slider_mode = 'left'
                self.is_dragging_duration_bar = True

            # right duration slider
            elif width // 2 + 1 <= x <= width - 180 and height - 154 <= y <= height - 140:
                self.song_duration_limit_update(event)
                self.duration_slider_mode = 'right'
                self.is_dragging_duration_bar = True

    def mouseMoveEvent(self, event):
        pos = event.pos()
        self.coord_label.setText(f"Mouse Coordinates: ({pos.x()}, {pos.y()})")

        if self.is_dragging_progress_bar is True:
            self.func_pause_music()
            self.song_progress_bar_update(event)

        elif self.is_dragging_duration_bar is True:
            self.song_duration_limit_update(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        state = self.player.playback_session.playback_state
        if event.button() == Qt.MouseButton.LeftButton:
            if (self.is_dragging_progress_bar or self.is_dragging_duration_bar) and state == 4 and self.manual_pause is False:
                self.func_unpause_music()
            self.is_dragging_progress_bar = False
            self.is_dragging_duration_bar = False

    def resizeEvent(self, event):
        self.update_button_positions()
        super().resizeEvent(event)

        self.overlay.resize(self.size())
        super().resizeEvent(event)

    def update_button_positions(self):
        new_width = self.width()
        new_height = self.height()

        # buttons
        self.play_button.move(new_width // 2 - 50, new_height - 100)
        self.next_button_widget.move(new_width // 2 + 40, int(new_height - 80))
        self.previous_button_widget.move(new_width // 2 - 100, int(new_height - 80))
        self.song_key_label.setFixedWidth(new_width - 180 * 2 - 35)
        self.search_song.move(new_width - 180 * 2 - 20, 27)
        self.download_song.move(new_width - 180 * 2 - 230, 27)

        # text
        self.song_title_text.move(100, new_height - 70)
        self.volume_number_text.move(310, new_height - 101)
        self.current_song_time_text.move(110, new_height - 211)
        self.total_song_time_text.move(new_width - 180 + 15, new_height - 211)

        self.background.setGeometry(0, 0, new_width, new_height)
        # self.background.setPixmap(QPixmap.fromImage(QImage("assets/app_background.png")))  # Set the modified image
        self.background.setScaledContents(True)

        # sliders
        self.volume_slider.move(101, new_height - 99)
        self.progress_bar_slider.move(180, new_height - 233)
        self.progress_bar_slider.setFixedWidth(new_width - 180 * 2)

        self.song_start_slider.move(180, new_height - 195)
        self.song_start_slider.setFixedWidth(new_width // 2 - 180)
        self.song_end_slider.move(new_width // 2, new_height - 195)
        self.song_end_slider.setFixedWidth(new_width // 2 - 180)

        # song buttons
        self.scroll_area.setGeometry(180, 70, new_width - 180 * 2, new_height - 280)
        for button in self.buttons:
            button.setFixedWidth(new_width - 180 * 2 - 100)

        # make sure the text doesnt overlap on buttons
        self.fix_overlapping_text()

    def global_timer_task_60s(self):
        Utils.update_json()

    def global_timer_task_03s(self):
        # play the next song
        state = self.player.playback_session.playback_state
        total = int(self.player.playback_session.natural_duration.total_seconds()) * 1000
        remaining = int(self.player.playback_session.position.total_seconds()) * 1000

        if state == 0 and self.manual_pause is False or 0 < total == remaining:
            config['previous_song'] = self.currently_playing_file
            self.progress_bar_slider.setValue(100000)
            self.current_song_time_text.setText(Utils.secunda(round(total / 1000)))
            self.total_song_time_text.setText("00:00")

            self.play_a_song(custom_song=config['next_song'] if config['next_song'] else None)
            config['next_song'] = None

        # update the song progress bar
        if state == 3:
            self.manual_pause = False
            print(config['total_played_counter'], remaining, self.prev_ms)
            remaining = int(self.player.playback_session.position.total_seconds()) * 1000

            if remaining > self.prev_ms:
                config['total_played_counter'] += remaining - self.prev_ms
            self.prev_ms = remaining

            self.progress_bar_slider.setValue(int((remaining / max(1, total)) * 100000))
            self.current_song_time_text.setText(Utils.secunda(round(remaining / 1000)))
            self.total_song_time_text.setText(Utils.secunda(round((total - remaining) / 1000)))

        self.update_song_progress_bar_stylesheet()

        # check if theres any custom end time
        try:
            if self.progress_bar_slider.value() >= config['modified_times'][self.currently_playing_file][1] * 100000:
                config['previous_song'] = self.currently_playing_file
                self.play_a_song(custom_song=config['next_song'] if config['next_song'] else None)
        except:
            pass

        # update the windows media controller thing
        smtc = self.player.system_media_transport_controls
        smtc.is_next_enabled = True
        smtc.is_previous_enabled = True
        smtc.is_stop_enabled = True
        self.update_metadata()

    def play_a_song(self, custom_song):
        current_file = custom_song if custom_song else Utils.next_ceva()
        self.currently_playing_file = current_file

        # very important play part
        async def load_song():
            file = await StorageFile.get_file_from_path_async(current_file.replace("/", "\\"))
            self.change_song_time(0, 0)
            media_source = MediaSource.create_from_storage_file(file)
            self.player.source = media_source

        asyncio.run(load_song())

        self.song_title_text.setText(current_file[len(config['music_path']):-4])


        if self.manual_pause is False:
            self.player.play()
            # wait until the song is loaded
            while self.player.playback_session.playback_state != 3:
                pass

            try:
                config['song_stats'][self.currently_playing_file]["number_of_plays"] += 1
            except:
                config["song_stats"][self.currently_playing_file] = {'duration': int(self.player.playback_session.natural_duration.total_seconds()) * 1000,
                                                                     'number_of_plays': 1
                                                                    }
            try:
                if config['modified_times'][current_file]:
                    percentage = config['modified_times'][current_file]
                    self.change_song_time(0, percentage[0] / 2)
                    self.progress_bar_slider.setValue(round(percentage[0] * 100000 / 2))
                    self.song_start_slider.setValue(round(percentage[0] * 100000))
                    self.song_end_slider.setValue(round(percentage[1] * 100000))  # funky logic

                    total = int(self.player.playback_session.natural_duration.total_seconds()) * 1000
                    remaining = int(self.player.playback_session.position.total_seconds()) * 1000

                    self.current_song_time_text.setText(Utils.secunda(round(remaining / 1000)))
                    self.total_song_time_text.setText(Utils.secunda(round((total - remaining) / 1000)))
            except: # a custom time hasnt been found
                self.song_start_slider.setValue(0)
                self.song_end_slider.setValue(100000)

            self.play_button.set_image("assets/pause_button.png")

        try:
            if config['modified_volumes'][current_file]:
                self.volume_slider.setValue(round((((config['modified_volumes'][current_file] * 100) / config['base_volume']) * 100)))
        except Exception:  # idek what this exception this gives it just crashes the program
            self.volume_slider.setValue(100)  # 100% of the base volume

        self.fix_overlapping_text()  # again make sure the text doesnt overlap on buttons

    def change_system_volume(self, value):
        if self.currently_playing_file:
            AudioController.change_system_volume_util(self.audio_controller, value / 100, self.currently_playing_file)

        self.volume_number_text.setText(str(round(value / 100 * config['base_volume'])))

    def change_song_time(self, value, percentage=0):
        total = int(self.player.playback_session.natural_duration.total_seconds()) * 1000

        if not percentage:
            percentage = value / 100000

        self.progress_bar_slider.setValue(int(100000 * percentage))
        self.player.playback_session.position = timedelta(milliseconds=int(total * percentage))
        remaining = int(self.player.playback_session.position.total_seconds()) * 1000

        self.current_song_time_text.setText(Utils.secunda(round(remaining / 1000)))
        self.total_song_time_text.setText(Utils.secunda(round((total - remaining) / 1000)))

    def play_pause_button(self):
        state = self.player.playback_session.playback_state

        if state == 3:
            self.player.pause()
            self.manual_pause = True
            self.play_button.set_image("assets/play_button.png")
        elif state == 4 or (state == 0 and self.manual_pause is True):
            self.player.play()
            self.manual_pause = False
            self.play_button.set_image("assets/pause_button.png")
        else:
            self.currently_playing_file = 1
            self.update_song_progress_bar_stylesheet(custom_percentage=1)
            self.play_a_song(custom_song=None)

        self.update_metadata()

    def next_button(self):
        if self.currently_playing_file:
            config['previous_song'] = self.currently_playing_file
        self.currently_playing_file = 1
        self.change_song_time(0)
        self.update_song_progress_bar_stylesheet(custom_percentage=1)
        self.play_a_song(custom_song=config['next_song'] if config['next_song'] else None)
        config['next_song'] = None

    def previous_button(self):
        if self.currently_playing_file and self.currently_playing_file != config['previous_song']:
            config['next_song'] = self.currently_playing_file

        self.currently_playing_file = 1
        self.change_song_time(0)
        self.update_song_progress_bar_stylesheet(custom_percentage=1)

        if config['previous_song']:
            self.play_a_song(custom_song=config['previous_song'])

    def update_metadata(self):
        # avoid crashing the program right as it starts
        if type(self.currently_playing_file) == int:
            return

        smtc = self.player.system_media_transport_controls
        async def get_file():
            try:
                thumbnail = fr"D:\Music Player\Music Player\thumbnails\{self.currently_playing_file[len(config['music_path']):-4]}.jpg"
                return await StorageFile.get_file_from_path_async(thumbnail)
            except:
                thumbnail = r"D:\Music Player\Music Player\assets\angery_miku.png"
                return await StorageFile.get_file_from_path_async(thumbnail)

        display_updater = smtc.display_updater
        display_updater.type = 1  # 1 = MediaPlaybackType.MUSIC
        display_updater.music_properties.title = self.currently_playing_file[len(config['music_path']):-4]
        display_updater.music_properties.artist = "Python"

        file = RandomAccessStreamReference.create_from_file(asyncio.run(get_file()))
        display_updater.thumbnail = file

        display_updater.update()

    def on_button_pressed(self, sender, args):
        print("Button pressed event received. Button:", args.button)
        if args.button == 0:  # unpause
            self.func_unpause_music()
            self.manual_pause = False
        elif args.button == 1:  # pause
            self.func_pause_music()
            self.manual_pause = True
        elif args.button == 6:  # next
            self.next_button()
        elif args.button == 7:  # previous
            self.previous_button()

        smtc = self.player.system_media_transport_controls
        smtc.is_next_enabled = True
        smtc.is_previous_enabled = True
        smtc.is_stop_enabled = True

        self.update_metadata()

class Utils:
    @staticmethod
    def next_ceva():
        path = config['music_path']
        files = os.listdir(path)
        files.remove('desktop.ini')
        current_file = path + random.choice(files)
        while current_file == config['previous_song']:
            current_file = path + random.choice(files)

        return current_file

    @staticmethod
    def update_json():
        global prev
        if prev == config:
            return  # avoid useless updating

        filename = "D:/Music Player/Music Player/config.json"
        temp_filename = filename + ".tmp"

        try:
            with open(temp_filename, "w") as temp_file:
                json.dump(config, temp_file, indent=4)

            os.replace(temp_filename, filename)
            prev = deepcopy(config)

        except Exception as e:
            print(f"Error saving config: {e}")
            if os.path.exists(temp_filename):
                os.remove(temp_filename)

    @staticmethod
    def resize_image(image_path, size=(64, 64)):
        img = Image.open(image_path)
        img = img.resize(size, Image.Resampling.LANCZOS)
        if image_path.startswith("thumbnails/"):
            return img

        return QPixmap.fromImage(ImageQt.ImageQt(img))

    @staticmethod
    def secunda(numar: int):
        ceva = "0"
        ceva += f"{numar // 60}:"
        if numar % 60 < 10:
            ceva += "0"
            ceva += f"{numar % 60}"
        else:
            ceva += f"{numar % 60}"

        return ceva

    @staticmethod
    def sort_songs(match: str):
        path = config['music_path']
        songs = [song for song in os.listdir(path) if song != 'desktop.ini']

        symbols, letters, others = [], [], []

        symbols_regex = r"[0-9!@#$%^&*]"
        letters_regex = r"[a-zA-Z]"

        for song in songs:
            if match.lower() in song.lower():
                if re.search(symbols_regex, song[0]):
                    symbols.append(song)
                elif re.search(letters_regex, song[0]):
                    letters.append(song)
                else:
                    others.append(song)

        return others + symbols + letters

    @staticmethod
    def is_overlapping_text(widget1: ClickableImage, widget2: QLabel):
        rect1 = widget1.geometry()

        font_metrics = QFontMetrics(widget2.font())
        text_width = font_metrics.horizontalAdvance(widget2.text())
        text_height = widget2.height()

        text_rect = QRect(widget2.x(), widget2.y(), text_width + 20, text_height)

        return rect1.intersects(text_rect)

    @staticmethod
    def sanitize_filename(filename):
        return re.sub(r'[<>:"\\/|?*]', '', filename)

    def download_link(self: MusicPlayer, url):
        try:
            print(self.overlay_download_button, self.overlay_button)
            if not self.overlay_download_button:
                self.overlay_download_button = self.popup.add_button("Please wait while downloading...")
            else:
                self.overlay_download_button.setText("Please wait while downloading...")

            # stupid library doesnt want to register the command so i have to do it twice
            QApplication.processEvents()
            QApplication.processEvents()

            ys = YouTube(url, 'WEB', on_progress_callback=on_progress, use_po_token=False)
        except:
            return -1
        yt = ys.streams.get_audio_only()
        thumbnails_save_path = 'thumbnails/'
        music_save_path = config['music_path']
        song_list = [song[:-4] for song in os.listdir(music_save_path)]
        title = Utils.sanitize_filename(ys.title)
        print(ys.thumbnail_url, title)

        if title in song_list:
            return f"{title} is already downloaded!"

        yt.download(output_path=music_save_path)
        os.makedirs(thumbnails_save_path, exist_ok=True)  # Create directory if it doesn't exist
        request.urlretrieve(ys.thumbnail_url, thumbnails_save_path + title + ".jpg")

        return f"Successfully downloaded {title}!"

    def download_playlist(self: MusicPlayer, url):
        try:
            print(self.overlay_download_button, self.overlay_button)
            if not self.overlay_download_button:
                self.overlay_download_button = self.popup.add_button("Please wait while downloading...")
            else:
                self.overlay_download_button.setText("Please wait while downloading...")

            # stupid library doesnt want to register the command so i have to do it twice
            QApplication.processEvents()
            QApplication.processEvents()

            ys = Playlist(url, 'WEB', use_po_token=False)
        except:
            return -1

        thumbnails_save_path = 'thumbnails/'
        music_save_path = config['music_path']
        song_list = [song[:-4] for song in os.listdir(music_save_path)]
        last_title = ''
        for i, video in enumerate(ys.videos, start=1):
            title = Utils.sanitize_filename(video.title)
            last_title = title
            try:
                audio = video.streams.get_audio_only()
            except:
                self.update_overlay_button(f"Couldn't get {title}")
            # except Exception as e:
            #     print(f"error at {e} {i} {title} ")
            #     continue
            if title in song_list:
                rez = f"{title} is already downloaded!"
                print(rez)
                if self.overlay_button:
                    self.update_overlay_button(rez)
                else:
                    self.overlay_button = self.popup.add_button(rez)
                    self.update_overlay_button(rez)
                QApplication.processEvents()
                QApplication.processEvents()

            else:
                audio.download(output_path=music_save_path)
                request.urlretrieve(video.thumbnail_url, thumbnails_save_path + title + ".jpg")
                rez = f"Successfully downloaded {title}!"
                if self.overlay_button:
                    self.update_overlay_button(rez)
                else:
                    self.overlay_button = self.popup.add_button(rez)
                    self.update_overlay_button(rez)
                    self.add_songs_to_layout(match="")
            QApplication.processEvents()
            QApplication.processEvents()

        return f"Successfully downloaded {last_title}!"

try:
    app = QApplication([])
    window = MusicPlayer()
    window.show()
    app.exec()
except Exception as e:
    print(e)