from __future__ import print_function, division
import numpy as np
import logging
import pdb
import numpy as np
from numpy.lib import stride_tricks
from Analysis import Analysis
from scipy import signal
from numpy.fft import fft, ifft, fftshift
from sppysound import multirate
import warnings

from numpy import polyfit, arange

class F0Analysis(Analysis):

    """
    F0 analysis descriptor class for generation of fundamental frequency
    estimation.

    This descriptor calculates the fundamental frequency for overlapping grains
    of an AnalysedAudioFile object.  A full definition of F0 analysis can be
    found in the documentation.

    Arguments:

    - analysis_group: the HDF5 file group to use for the storage of the
      analysis.

    - config: The configuration module used to configure the analysis
    """

    def __init__(self, AnalysedAudioFile, frames, analysis_group, config=None):
        super(F0Analysis, self).__init__(AnalysedAudioFile,frames, analysis_group, 'F0')
        self.logger = logging.getLogger(__name__+'.{0}Analysis'.format(self.name))
        # Store reference to the file to be analysed
        self.AnalysedAudioFile = AnalysedAudioFile

        self.nyquist_rate = self.AnalysedAudioFile.samplerate / 2.

        if config:
            self.window_size = config.f0["window_size"]
            self.overlap = 1. / config.f0["overlap"]
            self.threshold = config.f0["ratio_threshold"]
        else:
            self.window_size=512
            self.overlap = 0.5
            self.threshold = 0.

        self.analysis_group = analysis_group
        self.logger.info("Creating F0 analysis for {0}".format(self.AnalysedAudioFile.name))

        self.create_analysis(
            frames,
            self.AnalysedAudioFile.samplerate,
            window_size=self.window_size,
            overlapFac=self.overlap,
            threshold=config.f0["ratio_threshold"]
        )

    def get_analysis_grains(self, start, end):
        """
        Retrieve analysis frames for period specified in start and end times.
        arrays of start and end time pairs will produce an array of equivelant
        size containing frames for these times.
        """
        times = self.analysis_group["F0"]["times"][:]
        frames = self.analysis_group["F0"]["frames"][:]
        hr = self.analysis_group["F0"]["harmonic_ratio"][:]
        start = start / 1000
        end = end / 1000
        vtimes = times.reshape(-1, 1)
        nan_inds = hr < self.threshold
        hr[nan_inds] = np.nan
        frames[nan_inds] = np.nan

        selection = np.transpose((vtimes >= start) & (vtimes <= end))
        if not selection.any():
            frame_center = start + (end-start)/2.
            closest_frames = np.abs(vtimes-frame_center).argsort()[:2]
            selection[closest_frames] = True

        return ((frames, times, hr), selection)

    @staticmethod
    def create_f0_analysis(
        frames,
        samplerate,
        window_size=512,
        overlapFac=0.5,
        threshold=0.0,
        m0=None,
        M=None,
    ):
        """
        Generate F0 contour analysis.

        Calculate the frequency and harmonic ratio values of windowed segments
        of the audio file and save to disk.
        """
        if hasattr(frames, '__call__'):
            frames = frames()
        if not M:
            M=int(round(0.016*samplerate))


        hopSize = int(window_size - np.floor(overlapFac * window_size))

        # zeros at beginning (thus center of 1st window should be for sample nr. 0)
        samples = frames
        #samples = np.concatenate((np.zeros(np.floor(window_size/2.0)), frames))

        # cols for windowing
        cols = np.ceil((len(samples) - window_size) / float(hopSize)) + 1
        # zeros at end (thus samples can be fully covered by frames)
        samples = np.concatenate((samples, np.zeros(window_size)))

        frames = stride_tricks.as_strided(
            samples,
            shape=(cols, window_size),
            strides=(samples.strides[0]*hopSize, samples.strides[0])
        ).copy()

        # TODO: Replace this with zero crossing object.
        def feature_zcr(window):
            window2 = np.zeros(window.size)
            window2[1:-1] = window[0:-2]
            Z = (1/(2*window.size)) * np.sum(np.abs(np.sign(window)-np.sign(window2)))
            return Z

        def parabolic(f, x):
            """
            Quadratic interpolation for estimating the true position of an
            inter-sample maximum when nearby samples are known.

            f is a vector and x is an index for that vector.

            Returns (vx, vy), the coordinates of the vertex of a parabola that
            goes through point x and its two neighbors.

            Example:

            Defining a vector f with a local maximum at index 3 (= 6), find
            local maximum if points 2, 3, and 4 actually defined a parabola.

            In [3]: f = [2, 3, 1, 6, 4, 2, 3, 1]

            In [4]: parabolic(f, argmax(f))

            Out[4]: (3.2142857142857144, 6.1607142857142856)

            Ref: https://gist.github.com/endolith/255291

            """
            if x >= f.size-1 or x <= 2:
                return x, f[x]

            xv = 1/2. * (f[x-1] - f[x+1]) / (f[x-1] - 2 * f[x] + f[x+1]) + x
            yv = f[x] - 1/4. * (f[x-1] - f[x+1]) * (xv - x)
            return (xv, yv)

        def per_frame_f0(frames, m0, M):
            if not frames.any():
                HR = np.nan
                f0 = np.nan
                return f0, HR

            #R=autocorr([frames])[0]
            R = np.correlate(frames, frames, mode='full')
            g=R[frames.size]

            R=R[frames.size-1:]

            if not m0:
                # estimate m0 (as the first zero crossing of R)
                m0 = np.argmin(np.diff(np.sign(R[1:])))+1
            if m0 == 1:
                m0 = R.size
            if M > R.size:
                M = R.size
            Gamma = np.zeros(M)

            CSum = np.cumsum(frames*frames)
            with warnings.catch_warnings():
                warnings.filterwarnings('error')
                try:
                    Gamma[m0:M] = R[m0:M] / (np.sqrt([g*CSum[-m0:-M:-1]])+np.finfo(float).eps)
                except Warning:
                    pass

            # compute T0 and harmonic ratio:
            if np.isnan(Gamma).any():
                HR = np.nan
                f0 = np.nan
            else:
                blag = np.argmax(Gamma)
                HR = Gamma[blag]
                interp, HR = parabolic(Gamma, blag)
                if not interp:
                    f0 = np.nan
                    HR = np.nan
                else:
                    # get fundamental frequency:
                    f0 = samplerate / interp
            if f0 > samplerate/2:
                raise ValueError("F0 value ({0}) is above the nyquist rate "
                                 "({1}). This shouldn't happen...".format(f0,
                                 samplerate/2))
            if HR >= 1:
                HR = 1
            return (f0, HR)

        output = np.apply_along_axis(per_frame_f0, 1, frames, m0, M)
        # output = np.empty((frames.shape[0], 2))
        # for ind, i in enumerate(frames):
        #     output[ind] = per_frame_f0(i, m0, M)

        return output

    def hdf5_dataset_formatter(self, *args, **kwargs):
        '''
        Formats the output from the analysis method to save to the HDF5 file.
        '''
        samplerate = self.AnalysedAudioFile.samplerate
        frames = args[0]
        # frames = multirate.interp(frames, 4)
        samplerate *= 1
        data = self.create_f0_analysis(frames, samplerate, **kwargs)
        f0 = data[:, 0]
        harmonic_ratio = data[:, 1]
        f0_times = self.calc_f0_frame_times(f0, frames, samplerate)
        return ({'frames': f0, 'harmonic_ratio': harmonic_ratio, 'times': f0_times}, {})

    @staticmethod
    def calc_f0_frame_times(f0frames, sample_frames, samplerate):

        """Calculate times for frames using sample size and samplerate."""

        if hasattr(sample_frames, '__call__'):
            sample_frames = sample_frames()
        # Get number of frames for time and frequency
        timebins = f0frames.shape[0]
        # Create array ranging from 0 to number of time frames
        scale = np.arange(timebins+1)
        # divide the number of samples by the total number of frames, then
        # multiply by the frame numbers.
        f0_times = (sample_frames.shape[0]/timebins) * scale[:-1]
        # Divide by the samplerate to give times in seconds
        f0_times = f0_times / samplerate
        return f0_times

    def analysis_formatter(self, data, selection, format):
        """Calculate the average analysis value of the grain using the match format specified."""
        frames, times, harm_ratio = data
        # Get indexes of all valid frames (that aren't nan)
        valid_inds = np.isfinite(frames) & np.isfinite(harm_ratio)

        format_style_dict = {
            'mean': np.mean,
            'median': np.median,
            'log2_mean': self.log2_mean,
            'log2_median': self.log2_median,
        }

        if not selection.size:
            # TODO: Add warning here
            return np.nan

        #for ind, i in enumerate(selection):
        #    output[ind] = self.formatter_func(i, frames, valid_inds, harm_ratio, formatter=format_style_dict[format])

        try:
            output = np.apply_along_axis(
                self.formatter_func,
                1,
                selection,
                frames,
                valid_inds,
                formatter=format_style_dict[format]
            )/self.nyquist_rate
        except IndexError:
            pdb.set_trace()

        return output

