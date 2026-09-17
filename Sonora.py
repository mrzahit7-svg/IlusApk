import miniaudio


class Audio:
    def __init__(self, file, device):
        self.file = file
        self.device = device


def Play(file):
    stream = miniaudio.stream_file(
        file,
        output_format=miniaudio.SampleFormat.SIGNED16,
        nchannels=2,
        sample_rate=44100
    )

    device = miniaudio.PlaybackDevice(
        output_format=miniaudio.SampleFormat.SIGNED16,
        nchannels=2,
        sample_rate=44100
    )

    device.start(stream)

    return Audio(file, device)


def Stop(audio):
    if audio is None:
        return

    audio.device.close()


def Pause(audio):
    if audio is None:
        return

    audio.device.stop()


def Resume(audio):
    if audio is None:
        return

    audio.device.start()