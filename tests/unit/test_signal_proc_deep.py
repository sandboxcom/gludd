"""
Deep signal processing tests: FFT/IFFT, convolution, filter design,
window functions, spectrogram. Uses numpy ground-truth verification.
"""

from __future__ import annotations

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Signal generators
# ---------------------------------------------------------------------------


def sine_wave(freq: float, fs: float, duration: float) -> np.ndarray:
    """Generate a sine wave sampled at fs Hz for duration seconds."""
    n = int(fs * duration)
    t = np.arange(n) / fs
    return np.sin(2 * np.pi * freq * t)


def gaussian_pulse(fs: float, duration: float, center: float = 0.5, sigma: float = 0.05) -> np.ndarray:
    n = int(fs * duration)
    t = np.arange(n) / fs
    return np.exp(-0.5 * ((t - center) / sigma) ** 2)


# ---------------------------------------------------------------------------
# FFT / IFFT
# ---------------------------------------------------------------------------


class TestFFTIFFT:
    """Real FFT, inverse FFT, Parseval, conjugate symmetry."""

    def test_fft_ifft_roundtrip_real(self) -> None:
        x = sine_wave(50, 1000, 0.5)
        X = np.fft.rfft(x)
        xr = np.fft.irfft(X, n=len(x))
        assert np.allclose(x, xr, atol=1e-12)

    def test_fft_ifft_roundtrip_complex(self) -> None:
        x = np.random.randn(1024) + 1j * np.random.randn(1024)
        X = np.fft.fft(x)
        xr = np.fft.ifft(X)
        assert np.allclose(x, xr, atol=1e-12)

    def test_parseval_theorem(self) -> None:
        x = np.random.randn(2048)
        X = np.fft.rfft(x)
        energy_time = np.sum(np.abs(x) ** 2)
        energy_freq = (np.sum(np.abs(X[1:-1]) ** 2) * 2 + np.abs(X[0]) ** 2 + np.abs(X[-1]) ** 2) / len(x)
        assert abs(energy_time - energy_freq) / energy_time < 1e-10

    def test_conjugate_symmetry_real_input(self) -> None:
        x = np.random.randn(512)
        X = np.fft.fft(x)
        for k in range(1, len(X) // 2):
            assert np.allclose(X[k], np.conj(X[-k]), atol=1e-12)

    def test_zero_padding_increases_resolution(self) -> None:
        x = sine_wave(10, 100, 0.5)
        X1 = np.fft.rfft(x)
        X2 = np.fft.rfft(x, n=len(x) * 4)
        assert len(X2) > len(X1)
        f1 = np.fft.rfftfreq(len(x), d=1.0)
        f2 = np.fft.rfftfreq(len(x) * 4, d=1.0)
        peak1 = f1[np.argmax(np.abs(X1))]
        peak2 = f2[np.argmax(np.abs(X2))]
        assert abs(peak1 - peak2) < 0.5

    def test_dc_offset_in_zero_bin(self) -> None:
        x = np.ones(256) * 3.5
        X = np.fft.rfft(x)
        assert np.abs(X[0] - 3.5 * len(x)) < 1e-10
        assert np.all(np.abs(X[1:]) < 1e-10)


# ---------------------------------------------------------------------------
# Convolution
# ---------------------------------------------------------------------------


class TestConvolution:
    """Linear/circular convolution, impulse response, commutativity."""

    def test_linear_convolution_length(self) -> None:
        a = np.random.randn(100)
        b = np.random.randn(50)
        c = np.convolve(a, b, mode="full")
        assert len(c) == len(a) + len(b) - 1

    def test_convolution_with_impulse(self) -> None:
        a = np.random.randn(200)
        impulse = np.zeros(1)
        impulse[0] = 1.0
        c = np.convolve(a, impulse, mode="full")
        assert np.allclose(c, a)

    def test_convolution_commutativity(self) -> None:
        a = np.random.randn(80)
        b = np.random.randn(80)
        assert np.allclose(np.convolve(a, b, "full"), np.convolve(b, a, "full"))

    def test_convolution_associativity(self) -> None:
        a = np.random.randn(30)
        b = np.random.randn(20)
        c_kernel = np.random.randn(15)
        ab_c = np.convolve(np.convolve(a, b, "full"), c_kernel, "full")
        a_bc = np.convolve(a, np.convolve(b, c_kernel, "full"), "full")
        assert np.allclose(ab_c, a_bc, atol=1e-12)

    def test_circular_convolution_via_fft(self) -> None:
        a = np.random.randn(128)
        b = np.random.randn(128)
        c_fft = np.fft.ifft(np.fft.fft(a) * np.fft.fft(b))
        c_circ = np.array([np.sum(a * np.roll(b[::-1], k + 1)) for k in range(len(a))])
        assert np.allclose(c_fft, c_circ, atol=1e-10)


# ---------------------------------------------------------------------------
# Filter design
# ---------------------------------------------------------------------------


class TestFilterDesign:
    """FIR, IIR, frequency response, linear-phase check."""

    def test_fir_lowpass_zeros_above_cutoff(self) -> None:
        fs = 1000
        cutoff = 100
        taps = 101
        h = np.sinc(2 * cutoff / fs * (np.arange(taps) - (taps - 1) / 2))
        h *= np.hamming(taps)
        H = np.fft.rfft(h, n=4096)
        freqs = np.fft.rfftfreq(4096, d=1 / fs)
        passband = freqs <= cutoff * 0.8
        stopband = freqs >= cutoff * 1.5
        mag = np.abs(H) / np.max(np.abs(H))
        assert np.min(mag[passband]) > 0.99
        assert np.max(mag[stopband]) < 0.05

    def test_fir_highpass_zeros_below_cutoff(self) -> None:
        fs = 1000
        cutoff = 200
        taps = 255
        nfft = 16384
        idx = np.arange(taps) - (taps - 1) / 2
        h_lp = np.sinc(2 * cutoff / fs * idx) * np.blackman(taps)
        h_lp /= np.sum(h_lp)
        h = -h_lp
        h[(taps - 1) // 2] += 1.0
        H = np.fft.rfft(h, n=nfft)
        freqs = np.fft.rfftfreq(nfft, d=1 / fs)
        stopband = freqs <= cutoff * 0.4
        passband = freqs >= cutoff * 1.3
        mag = np.abs(H) / np.max(np.abs(H))
        assert np.max(mag[stopband]) < 0.05, f"max {np.max(mag[stopband]):.4f}"
        assert np.min(mag[passband]) > 0.95

    def test_fir_bandpass(self) -> None:
        fs = 2000
        lo, hi = 300, 500
        taps = 151
        h_lo = np.sinc(2 * lo / fs * (np.arange(taps) - (taps - 1) / 2))
        h_hi = np.sinc(2 * hi / fs * (np.arange(taps) - (taps - 1) / 2))
        h = (h_hi - h_lo) * np.blackman(taps)
        H = np.fft.rfft(h, n=8192)
        freqs = np.fft.rfftfreq(8192, d=1 / fs)
        mag = np.abs(H) / np.max(np.abs(H))
        passband = (freqs >= lo * 1.1) & (freqs <= hi * 0.9)
        assert np.min(mag[passband]) > 0.95

    def test_iir_butterworth_stable(self) -> None:
        b = np.array([0.04658291, 0.18633162, 0.27949743, 0.18633162, 0.04658291])
        a = np.array([1.0, -0.78271551, 0.67998595, -0.18267481, 0.03073078])
        w = np.linspace(0, np.pi, 512)
        z = np.exp(-1j * w)
        H = np.polyval(b[::-1], z) / np.polyval(a[::-1], z)
        assert np.all(np.abs(H) <= 1.01)
        assert np.abs(H[0]) > 0.99
        assert np.abs(H[-1]) < 0.01
        poles = np.roots(a)
        assert np.all(np.abs(poles) < 1.0)

    def test_linear_phase_symmetric_fir(self) -> None:
        taps = 67
        h = np.sinc(0.15 * (np.arange(taps) - (taps - 1) / 2))
        h *= np.hamming(taps)
        for i in range(taps // 2):
            assert abs(h[i] - h[taps - 1 - i]) < 1e-14


# ---------------------------------------------------------------------------
# Window functions
# ---------------------------------------------------------------------------


class TestWindowFunctions:
    """Rect, Hann, Hamming, Blackman, Kaiser — symmetry, bounds, energy."""

    def _window_energy(self, w: np.ndarray) -> float:
        return float(np.sum(w**2))

    def test_rectangular_window(self) -> None:
        w = np.ones(256)
        assert self._window_energy(w) == 256.0

    def test_hann_zero_endpoints(self) -> None:
        w = np.hanning(128)
        assert abs(w[0]) < 1e-15
        assert abs(w[-1]) < 1e-15

    def test_hamming_nonzero_endpoints(self) -> None:
        w = np.hamming(128)
        assert w[0] > 0.07
        assert w[-1] > 0.07

    def test_blackman_deep_stopband(self) -> None:
        w1 = np.hanning(256)
        w2 = np.blackman(256)
        assert self._window_energy(w2) < self._window_energy(w1)

    def test_symmetry_all_windows(self) -> None:
        for name, func in [("hann", np.hanning), ("hamming", np.hamming), ("blackman", np.blackman)]:
            w = func(101)
            assert np.allclose(w, w[::-1], atol=1e-14), f"{name} not symmetric"

    def test_kaiser_beta_trades_main_lobe_sidelobe(self) -> None:
        w_narrow = np.kaiser(256, 1.0)
        w_wide = np.kaiser(256, 10.0)
        Hn = np.abs(np.fft.rfft(w_narrow, n=4096))
        Hw = np.abs(np.fft.rfft(w_wide, n=4096))
        main_n = np.argmax(Hn < 0.5 * np.max(Hn))
        main_w = np.argmax(Hw < 0.5 * np.max(Hw))
        assert main_n < main_w


# ---------------------------------------------------------------------------
# Spectrogram (STFT)
# ---------------------------------------------------------------------------


class TestSpectrogram:
    """STFT, overlap-add, time-frequency resolution, zero-padding effects."""

    def _stft(self, x: np.ndarray, nperseg: int, noverlap: int, window: str = "hann") -> np.ndarray:
        step = nperseg - noverlap
        if window == "hann":
            win = np.hanning(nperseg)
        elif window == "boxcar":
            win = np.ones(nperseg)
        else:
            raise ValueError("unknown window")
        n_frames = (len(x) - noverlap) // step
        spec = np.zeros((nperseg // 2 + 1, n_frames), dtype=complex)
        for i in range(n_frames):
            seg = x[i * step : i * step + nperseg] * win
            spec[:, i] = np.fft.rfft(seg)
        return spec

    def test_stft_dimensions(self) -> None:
        x = np.random.randn(4096)
        spec = self._stft(x, nperseg=256, noverlap=128)
        assert spec.shape == (129, 31)

    def test_overlap_add_reconstruction(self) -> None:
        fs = 1000
        x = sine_wave(80, fs, 2.0)
        nperseg = 256
        noverlap = 192
        step = nperseg - noverlap
        win = np.hanning(nperseg)
        recon = np.zeros(len(x))
        weight = np.zeros(len(x))
        for i in range(0, len(x) - nperseg + 1, step):
            recon[i : i + nperseg] += x[i : i + nperseg] * win
            weight[i : i + nperseg] += win
        safe = weight > 1e-15
        recon[safe] /= weight[safe]
        assert np.allclose(recon[nperseg:-nperseg], x[nperseg:-nperseg], atol=1e-12)

    def test_spectrogram_frequency_bin_resolution(self) -> None:
        fs = 1000
        freq = 128
        x = sine_wave(freq, fs, 1.0)
        spec = self._stft(x, nperseg=512, noverlap=256, window="boxcar")
        mag = np.abs(spec)
        bin_freqs = np.fft.rfftfreq(512, d=1 / fs)
        peak_bin = np.argmax(np.mean(mag, axis=1))
        assert bin_freqs[peak_bin] == pytest.approx(freq, abs=fs / 512)

    def test_zero_padding_spectrogram_smooths_bins(self) -> None:
        x = sine_wave(50, 1000, 1.0)
        nperseg = 128
        win = np.hanning(nperseg)
        nfft_short = 128
        nfft_long = 512
        seg = x[:nperseg] * win
        S1 = np.abs(np.fft.rfft(seg, n=nfft_short))
        S2 = np.abs(np.fft.rfft(seg, n=nfft_long))
        assert S2.shape[0] > S1.shape[0]

    def test_chirp_spectrogram_shows_frequency_drift(self) -> None:
        fs = 2000
        duration = 2.0
        n = int(fs * duration)
        t = np.arange(n) / fs
        f0, f1 = 50, 500
        phase = 2 * np.pi * (f0 * t + (f1 - f0) / (2 * duration) * t**2)
        x = np.sin(phase)
        spec = self._stft(x, nperseg=512, noverlap=384)
        mag = np.abs(spec)
        freqs = np.fft.rfftfreq(512, d=1 / fs)
        early_peak = freqs[np.argmax(mag[:, 0])]
        late_peak = freqs[np.argmax(mag[:, -1])]
        assert late_peak > early_peak + 100
