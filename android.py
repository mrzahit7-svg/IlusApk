import sys
import re
import socket
import traceback
from termcolor import colored
from PySide6 import QtWidgets, QtCore, QtGui

from PySide6.QtMultimedia import (
    QCamera,
    QMediaCaptureSession,
    QMediaDevices,
)

from PySide6.QtMultimediaWidgets import (
    QVideoWidget,
)

from PySide6.QtPositioning import (
    QGeoPositionInfoSource,
)

from ilus_pyside import (
    run_on_gui_thread,
    run_on_gui_thread_sync,
    claim_gui_window,
    release_gui_window,
)


# =========================================================
# GUI WINDOW SLOT
# =========================================================

_GUI_LIBRARY_NAME = "android"
_GUI_COOPERATIVE = True


def ClaimGUIWindow(cooperative=True):
    """
    android kitabxanası üçün GUI window slot tələb edir.

    cooperative=True:
        Başqa cooperative kitabxanalarla paylaşa bilər.

    cooperative=False:
        Exclusive GUI slot tələb edir.
    """

    return claim_gui_window(
        _GUI_LIBRARY_NAME,
        cooperative=bool(cooperative)
    )


def ReleaseGUIWindow():
    """
    android kitabxanasının GUI window slotunu buraxır.
    """

    return release_gui_window(
        _GUI_LIBRARY_NAME
    )


# =========================================================
# CAMERA
# =========================================================

_camera = None
_session = None
_video = None
_window = None

_camera_gui_claimed = False


def _create_camera(width, height):
    global _camera
    global _session
    global _video
    global _window

    devices = QMediaDevices.videoInputs()

    if not devices:
        raise RuntimeError(
            "Android camera tapılmadı!"
        )

    width = int(width)
    height = int(height)

    _window = QtWidgets.QWidget()
    _window.setFixedSize(width, height)

    _video = QVideoWidget(_window)
    _video.setGeometry(
        0,
        0,
        width,
        height
    )
    _video.setFixedSize(
        width,
        height
    )

    _camera = QCamera(devices[0])

    _session = QMediaCaptureSession()

    _session.setCamera(
        _camera
    )

    _session.setVideoOutput(
        _video
    )

    _camera.start()


def _camera_on(width, height):
    global _camera
    global _window
    global _video
    global _camera_gui_claimed

    width = int(width)
    height = int(height)

    if not _camera_gui_claimed:

        if not claim_gui_window(
            _GUI_LIBRARY_NAME,
            cooperative=_GUI_COOPERATIVE
        ):
            raise RuntimeError(
                "ANDROID CAMERA ERROR:\n\n"
                "GUI window slot başqa kitabxana "
                "tərəfindən tutulub."
            )

        _camera_gui_claimed = True

    if _camera is None:

        _create_camera(
            width,
            height
        )

    else:

        _window.setFixedSize(
            width,
            height
        )

        _video.setFixedSize(
            width,
            height
        )

        _video.setGeometry(
            0,
            0,
            width,
            height
        )

    if not _camera.isActive():
        _camera.start()

    _window.show()


def _camera_off():
    global _camera

    if _camera is None:
        return

    _window.hide()
    _camera.stop()


def Camera(
    value,
    width=300,
    height=300
):
    def operation():

        app = QtCore.QCoreApplication.instance()

        if app is None:
            raise RuntimeError(
                "ANDROID CAMERA ERROR:\n\n"
                "QApplication tapılmadı."
            )

        permission = QtCore.QCameraPermission()

        def permission_result():

            try:

                if value:
                    _camera_on(
                        width,
                        height
                    )

                else:
                    _camera_off()

            except Exception:

                print(
                    "ANDROID CAMERA ERROR:\n\n"
                    + traceback.format_exc()
                )

        app.requestPermission(
            permission,
            None,
            permission_result
        )

    return run_on_gui_thread(
        operation
    )


# =========================================================
# BACK CAMERA / FRONT CAMERA
# =========================================================

_back_camera = None
_back_session = None
_back_video = None
_back_window = None

_back_camera_gui_claimed = False


def _create_back_camera(width, height):

    global _back_camera
    global _back_session
    global _back_video
    global _back_window

    devices = QMediaDevices.videoInputs()

    if not devices:
        raise RuntimeError(
            "Android camera tapılmadı!"
        )

    front = None

    for device in devices:

        try:

            camera_id = (
                device.id()
                .data()
                .decode()
            )

        except Exception:

            camera_id = ""

        if camera_id == "front":
            front = device
            break

    if front is None:
        raise RuntimeError(
            "Ön kamera tapılmadı!"
        )

    width = int(width)
    height = int(height)

    _back_window = QtWidgets.QWidget()

    _back_window.setFixedSize(
        width,
        height
    )

    _back_video = QVideoWidget(
        _back_window
    )

    _back_video.setGeometry(
        0,
        0,
        width,
        height
    )

    _back_video.setFixedSize(
        width,
        height
    )

    _back_camera = QCamera(
        front
    )

    _back_session = QMediaCaptureSession()

    _back_session.setCamera(
        _back_camera
    )

    _back_session.setVideoOutput(
        _back_video
    )

    _back_camera.start()


def _back_camera_on(
    width,
    height
):
    global _back_camera
    global _back_window
    global _back_video
    global _back_camera_gui_claimed

    width = int(width)
    height = int(height)

    if not _back_camera_gui_claimed:

        if not claim_gui_window(
            _GUI_LIBRARY_NAME,
            cooperative=_GUI_COOPERATIVE
        ):
            raise RuntimeError(
                "ANDROID BACK CAMERA ERROR:\n\n"
                "GUI window slot başqa kitabxana "
                "tərəfindən tutulub."
            )

        _back_camera_gui_claimed = True

    if _back_camera is None:

        _create_back_camera(
            width,
            height
        )

    else:

        _back_window.setFixedSize(
            width,
            height
        )

        _back_video.setFixedSize(
            width,
            height
        )

        _back_video.setGeometry(
            0,
            0,
            width,
            height
        )

    if not _back_camera.isActive():
        _back_camera.start()

    _back_window.show()


def _back_camera_off():

    global _back_camera

    if _back_camera is None:
        return

    _back_window.hide()
    _back_camera.stop()


def BackCamera(
    value,
    width=300,
    height=300
):
    def operation():

        app = QtCore.QCoreApplication.instance()

        if app is None:
            raise RuntimeError(
                "ANDROID BACK CAMERA ERROR:\n\n"
                "QApplication tapılmadı."
            )

        permission = QtCore.QCameraPermission()

        def permission_result():

            try:

                if value:

                    _back_camera_on(
                        width,
                        height
                    )

                else:

                    _back_camera_off()

            except Exception:

                print(
                    "ANDROID BACK CAMERA ERROR:\n\n"
                    + traceback.format_exc()
                )

        app.requestPermission(
            permission,
            None,
            permission_result
        )

    return run_on_gui_thread(
        operation
    )


# =========================================================
# FLASHLIGHT
# =========================================================

def Flashlight(value):

    def operation():

        try:

            global _camera

            if _camera is None:

                _create_camera(
                    1,
                    1
                )

            if not _camera.isActive():
                _camera.start()

            if value:

                _camera.setTorchMode(
                    QCamera.TorchOn
                )

            else:

                _camera.setTorchMode(
                    QCamera.TorchOff
                )

            if _window is not None:
                _window.hide()

            return True

        except Exception:

            raise RuntimeError(
                "ANDROID FLASHLIGHT ERROR:\n\n"
                + traceback.format_exc()
            )

    return run_on_gui_thread(
        operation
    )


# =========================================================
# SCREEN SIZE
# =========================================================

def ScreenSize():

    app = QtCore.QCoreApplication.instance()

    if app is None:
        raise RuntimeError(
            "ANDROID SCREEN ERROR:\n\n"
            "QApplication tapılmadı!"
        )

    screen = app.primaryScreen()

    if screen is None:
        raise RuntimeError(
            "ANDROID SCREEN ERROR:\n\n"
            "Əsas ekran tapılmadı!"
        )

    size = screen.size()

    return (
        size.width(),
        size.height()
    )


# =========================================================
# ORIENTATION
# =========================================================

def Orientation():

    app = QtCore.QCoreApplication.instance()

    if app is None:
        raise RuntimeError(
            "ANDROID ORIENTATION ERROR:\n\n"
            "QApplication tapılmadı!"
        )

    screen = app.primaryScreen()

    if screen is None:
        raise RuntimeError(
            "ANDROID ORIENTATION ERROR:\n\n"
            "Əsas ekran tapılmadı!"
        )

    orientation = screen.orientation()

    if orientation in (
        QtCore.Qt.ScreenOrientation.PortraitOrientation,
        QtCore.Qt.ScreenOrientation.InvertedPortraitOrientation
    ):

        return "portrait"

    if orientation in (
        QtCore.Qt.ScreenOrientation.LandscapeOrientation,
        QtCore.Qt.ScreenOrientation.InvertedLandscapeOrientation
    ):

        return "landscape"

    return "unknown"


# =========================================================
# DEVICE INFO
# =========================================================

def DeviceInfo():

    app = QtCore.QCoreApplication.instance()

    if app is None:
        raise RuntimeError(
            "ANDROID DEVICE ERROR:\n\n"
            "QApplication tapılmadı!"
        )

    return {

        "platform":
            QtCore.QSysInfo.productType(),

        "os":
            QtCore.QSysInfo.prettyProductName(),

        "architecture":
            QtCore.QSysInfo.currentCpuArchitecture(),

        "kernel":
            QtCore.QSysInfo.kernelType(),

        "kernel_version":
            QtCore.QSysInfo.kernelVersion()
    }


# =========================================================
# INTERNET
# =========================================================

def Internet():

    try:

        socket.create_connection(
            (
                "www.google.com",
                80
            ),
            timeout=3
        ).close()

        return True

    except Exception:

        return False


# =========================================================
# DEVICE NAME
# =========================================================

def DeviceName():

    app = QtCore.QCoreApplication.instance()

    if app is None:
        raise RuntimeError(
            "ANDROID DEVICE NAME ERROR:\n\n"
            "QApplication tapılmadı."
        )

    name = (
        QtCore.QSysInfo.machineHostName()
    )

    if not name:

        name = (
            QtCore.QSysInfo.prettyProductName()
        )

    return name


# =========================================================
# SCREEN DPI
# =========================================================

def ScreenDPI():

    app = QtCore.QCoreApplication.instance()

    if app is None:
        raise RuntimeError(
            "ANDROID SCREEN DPI ERROR:\n\n"
            "QApplication tapılmadı!"
        )

    screen = app.primaryScreen()

    if screen is None:
        raise RuntimeError(
            "ANDROID SCREEN DPI ERROR:\n\n"
            "Əsas ekran tapılmadı!"
        )

    return screen.logicalDotsPerInch()


# =========================================================
# ANDROID SDK
# =========================================================

def _get_android_sdk():

    try:

        info = DeviceInfo()

        os_name = info["os"]

        match = re.search(
            r"Android\s+(\d+(?:\.\d+)?)",
            os_name
        )

        if not match:
            return None

        android_version = float(
            match.group(1)
        )

        sdk_map = {

            9.0: 28,
            10.0: 29,
            11.0: 30,
            12.0: 31,
            12.1: 32,
            13.0: 33,
            14.0: 34,
            15.0: 35,
            16.0: 36
        }

        return sdk_map.get(
            android_version
        )

    except Exception:

        return None


def SDK(
    min_sdk,
    max_sdk
):

    try:

        min_sdk = int(
            min_sdk
        )

        max_sdk = int(
            max_sdk
        )

    except Exception:

        raise TypeError(
            "SDK ERROR:\n\n"
            "min və max int olmalıdır."
        )

    if min_sdk > max_sdk:

        raise ValueError(
            "SDK ERROR:\n\n"
            "minimum SDK maksimum SDK-dan "
            "böyük ola bilməz."
        )

    current_sdk = _get_android_sdk()

    if current_sdk is None:
        return False

    return (
        min_sdk
        <= current_sdk
        <= max_sdk
    )


# =========================================================
# LOCATION
# =========================================================

_location_source = None
_location_data = None


def _location_updated(info):

    global _location_data

    try:

        coordinate = info.coordinate()

        if not coordinate.isValid():
            return

        _location_data = {

            "latitude":
                coordinate.latitude(),

            "longitude":
                coordinate.longitude(),

            "altitude":
                coordinate.altitude()
        }

    except Exception:

        print(
            "ANDROID LOCATION ERROR:\n\n"
            + traceback.format_exc()
        )


def _location_error(error):

    print(
        "ANDROID LOCATION ERROR:\n\n"
        + str(error)
    )


def _start_location():

    global _location_source

    if _location_source is not None:
        return

    _location_source = (
        QGeoPositionInfoSource
        .createDefaultSource(None)
    )

    if _location_source is None:

        raise RuntimeError(
            "ANDROID LOCATION ERROR:\n\n"
            "Location source tapılmadı."
        )

    _location_source.positionUpdated.connect(
        _location_updated
    )

    _location_source.errorOccurred.connect(
        _location_error
    )

    _location_source.startUpdates()

    _location_source.requestUpdate(
        10000
    )


def Location():

    def operation():

        try:

            _start_location()

        except Exception:

            print(
                "ANDROID LOCATION ERROR:\n\n"
                + traceback.format_exc()
            )

    run_on_gui_thread(
        operation
    )

    return _location_data


# =========================================================
# LANGUAGE
# =========================================================

def Language():

    app = QtCore.QCoreApplication.instance()

    if app is None:
        raise RuntimeError(
            "ANDROID LANGUAGE ERROR:\n\n"
            "QApplication tapılmadı!"
        )

    locale = QtCore.QLocale.system()

    language = locale.language()

    language_map = {

        QtCore.QLocale.Language.Azerbaijani:
            "az",

        QtCore.QLocale.Language.English:
            "en",

        QtCore.QLocale.Language.Turkish:
            "tr",

        QtCore.QLocale.Language.Russian:
            "ru",

        QtCore.QLocale.Language.German:
            "de",

        QtCore.QLocale.Language.French:
            "fr",

        QtCore.QLocale.Language.Spanish:
            "es",

        QtCore.QLocale.Language.Arabic:
            "ar",

        QtCore.QLocale.Language.Persian:
            "fa",
    }

    return language_map.get(
        language,
        locale.name().split("_")[0]
    )


# =========================================================
# CLIPBOARD
# =========================================================

def Clipboard(text=None):

    app = QtCore.QCoreApplication.instance()

    if app is None:

        raise RuntimeError(
            "ANDROID CLIPBOARD ERROR:\n\n"
            "QApplication tapılmadı!"
        )

    def operation():

        clipboard = app.clipboard()

        if text is None:
            return clipboard.text()

        clipboard.setText(
            str(text)
        )

        return True

    return run_on_gui_thread_sync(
        operation
    )


# =========================================================
# LICENSE
# =========================================================

def License():

    return colored(
        "it is licensed by Zahid Ahmadov "
        "and it can use to get experience "
        "in beginner ilus developers and "
        "it is making better ilus library ecosystem",
        "red"
    )


# =========================================================
# OPEN URL
# =========================================================

def OpenURL(url):

    try:

        url = str(url)

        if not url:

            raise ValueError(
                "ANDROID OPEN URL ERROR:\n\n"
                "URL boş ola bilməz."
            )

        qurl = QtCore.QUrl(url)

        if not qurl.isValid():

            raise ValueError(
                "ANDROID OPEN URL ERROR:\n\n"
                "Yanlış URL."
            )

        def operation():

            if not QtGui.QDesktopServices.openUrl(
                qurl
            ):

                raise RuntimeError(
                    "ANDROID OPEN URL ERROR:\n\n"
                    "URL açıla bilmədi."
                )

            return True

        return run_on_gui_thread_sync(
            operation
        )

    except Exception:

        raise RuntimeError(
            "ANDROID OPEN URL ERROR:\n\n"
            + traceback.format_exc()
        )