from __future__ import print_function, division
import os
import shutil
import collections
from scipy import signal
import numpy as np
import pysndfile
import matplotlib.pyplot as plt
import pdb
import sys
import traceback
import logging
import h5py
import multiprocessing as mp

from fileops import pathops
from audiofile import AnalysedAudioFile, AudioFile
import analysis.RMSAnalysis as RMSAnalysis
import analysis.AttackAnalysis as AttackAnalysis
import analysis.ZeroXAnalysis as ZeroXAnalysis
import analysis.FFTAnalysis as FFTAnalysis
import analysis.SpectralCentroidAnalysis as SpectralCentroidAnalysis
import analysis.SpectralSpreadAnalysis as SpectralSpreadAnalysis
import analysis.F0Analysis as F0Analysis

logger = logging.getLogger(__name__).addHandler(logging.NullHandler())

class AudioDatabase:

    """A class for encapsulating a database of AnalysedAudioFile objects."""

    def __init__(
        self,
        audio_dir=None,
        db_dir=None,
        analysis_list=[],
        *args,
        **kwargs
    ):
        """
        Create the folder hierachy for the database of files to be stored in.

        Adds any pre existing audio files and analyses to the object
        automatically.
        audio_dir:
        self.db_dir:
        analysis_list:
        """
        self.db_dir = db_dir
        self.audio_dir = audio_dir
        self.analysis_list = analysis_list
        self.config = kwargs.pop("config", None)
        self.logger = logging.getLogger(__name__ + '.AudioDatabase')

        # Check that all analysis list args are valid
        valid_analyses = {'rms', 'zerox', 'fft', 'spccntr', 'spcsprd', 'f0'}
        for analysis in analysis_list:
            if analysis not in valid_analyses:
                raise ValueError("\'{0}\' is not a valid analysis type".format(analysis))

        # Filter out repetitions in list if they exist
        self.analysis_list = set(self.analysis_list)

        self.logger.info("Initialising Database...")

        # Create empty list to fill with audio file paths
        self.audio_file_list = OrderedSet()

        self.data = None

    def load_database(self, reanalyse=False):
        """Create/Read from a pre-existing database"""

        subdir_paths = self.create_subdirs()

        if self.audio_dir:
            # Check that audio directory exists
            if not os.path.exists(self.audio_dir):
                raise IOError("The audio directory provided ({0}) doesn't "
                            "exist").format(self.audio_dir)
            self.organize_audio(subdir_paths)

        analysed_audio = self.analyse_database(subdir_paths, reanalyse)

    def analyse_database(self, subdir_paths, reanalyse):
        # Create data file for storing analysis data for the database
        datapath = os.path.join(subdir_paths['data'], 'analysis_data.hdf5')
        self.data = h5py.File(datapath, 'a')
        self.analysed_audio = []

        for item in self.audio_file_list:
            filepath = os.path.join(subdir_paths['audio'], item)
            print("--------------------------------------------------")
            # if there is no wav file then skip
            try:
                with AnalysedAudioFile(
                    filepath,
                    'r',
                    analyses=self.analysis_list,
                    name=os.path.basename(item),
                    db_dir=self.db_dir,
                    data_file=self.data,
                    reanalyse=reanalyse,
                    config=self.config
                ) as AAF:
                    AAF.create_analysis()
                    self.analysed_audio.append(AAF)
            except IOError as err:
                # Skip any audio file objects that can't be analysed
                self.logger.warning("File cannot be analysed: {0}\nReason: {1}\n"
                      "Skipping...".format(item, err))
                exc_type, exc_value, exc_traceback = sys.exc_info()
                traceback.print_exception(exc_type, exc_value, exc_traceback,
                                          file=sys.stdout)
                continue
        print("--------------------------------------------------")
        self.logger.debug("Analysis Finished.")

    def add_file(self, file_object):
        '''Add an AnalysedAudioFile object to the database'''
        if type(file_object) is AnalysedAudioFile:
            self.analysed_audio.add(file_object)
            self.audio_file_list.append(file_object.filepath)
        else:
            raise TypeError("Object {0} of type {1} cannot be added to the database".format(file_object, file_object.__class__.__name__))

    def create_subdirs(self):

        # If the database directory isnt specified then the directory where the
        # audio files are stored will be used
        if not self.db_dir:
            if not self.audio_dir:
                raise IOError("No database location specified. Either a "
                              "database ocation or audio file location must be"
                              " specified.")
            self.db_dir = self.audio_dir


        # Check to see if the database directory already exists
        # Create if not
        pathops.dir_must_exist(self.db_dir)

        def initialise_subdir(dirkey):
            """
            Create a subdirectory in the database with the name of the key
            provided.
            Returns the path to the created subdirectory.
            """
            # Make sure database subdirectory exists
            directory = os.path.join(self.db_dir, dirkey)
            try:
                # If it doesn't, Create it.
                os.mkdir(directory)
                self.logger.info(''.join(("Created directory: ", directory)))
            except OSError as err:
                # If it does exist, add it's content to the database content
                # dictionary.
                if os.path.exists(directory):
                    self.logger.warning("\'{0}\' directory already exists:"
                    " {1}".format(dirkey, os.path.relpath(directory)))
                    if dirkey == 'audio':
                        for item in pathops.listdir_nohidden(directory):
                            self.audio_file_list.add(item)
                else:
                    raise err
            return directory

        # Create a sub directory for every key in the analysis list
        # store reference to this in dictionary
        self.logger.info("Creating sub-directories...")
        directory_set = {'audio', 'data'}
        subdir_paths = {
            key: initialise_subdir(key) for key in directory_set
        }
        # Save sub-directory paths for later access
        self.subdirs = subdir_paths
        return subdir_paths

    def organize_audio(self, subdir_paths, symlink=True):
        self.logger.info("Moving any audio to sub directory...")

        valid_filetypes = {'.wav', '.aif', '.aiff'}
        # Move audio files to database
        # For all files in the audio dirctory...
        for root, directories, filenames in os.walk(self.audio_dir):
            for item in filenames:
                # If the file is a valid file type...
                item = os.path.join(root,item)
                if os.path.splitext(item)[1] in valid_filetypes:
                    self.logger.debug(''.join(("File added to database content: ", item)))
                    # Get the full path for the file
                    filepath = os.path.join(self.audio_dir, item)
                    # If the file isn't already in the database...
                    if not os.path.isfile(
                        '/'.join((subdir_paths["audio"], os.path.basename(filepath)))
                    ):
                        # Copy the file to the database
                        if symlink:
                            filename = os.path.basename(filepath)
                            os.symlink(filepath, os.path.join(subdir_paths["audio"], filename))
                            self.logger.info(''.join(("Linked: ", item, "\tTo directory: ",
                                subdir_paths["audio"], "\n")))
                        else:
                            shutil.copy2(filepath, subdir_paths["audio"])
                            self.logger.info(''.join(("Moved: ", item, "\tTo directory: ",
                                subdir_paths["audio"], "\n")))

                    else:
                        self.logger.info(''.join(("File:  ", item, "\tAlready exists at: ",
                            subdir_paths["audio"])))
                    # Add the file's path to the database content dictionary
                    self.audio_file_list.add(
                        os.path.join(subdir_paths["audio"], item)
                    )

    def close(self):
        self.data.close()

    def __enter__(self):
        return self

    def __exit__(self):
        self.close()

class Matcher:

    """
    Database comparison object.

    Used to compare and match entries in two AnalysedAudioFile databases.
    """

    def __init__(self, database1, database2, analysis_dict,*args, **kwargs):
        self.config = kwargs.pop('config', None)
        self.match_quantity = kwargs.pop('quantity', 30)
        self.logger = logging.getLogger(__name__ + '.Matcher')
        self.source_db = database1
        self.target_db = database2
        self.output_db = kwargs.pop("output_db", None)
        self.rematch = kwargs.pop("rematch", False)

        self.analysis_dict = analysis_dict
        self.common_analyses = []
        """
        self.match_type = {
            "mean": self.mean_formatter,
            "median": self.median_formatter
        }
        """

        self.logger.debug("Initialised Matcher")

    def match(self, match_function, grain_size, overlap):
        """
        Find the closest match to each object in database 1 in database 2 using the matching function specified.
        """

        # Find all analyses shared by both the source and target entry
        common_analyses = self.source_db.analysis_list & self.target_db.analysis_list
        self.matcher_analyses = []
        # Create final list of analyses to perform matching on based on
        # selected match analyses.
        for key in self.analysis_dict.iterkeys():
            if key not in common_analyses:
                self.logger.warning("Analysis: \"{0}\" not avilable in {1} and/or {2}".format(key, source_entry, target_entry))
            else:
                self.matcher_analyses.append(key)

        # Run matching
        match_function(grain_size, overlap)

    def count_grains(self, database, grain_length, overlap):
        '''Calculate the number of grains in the database'''
        entry_count = len(database.analysed_audio)
        grain_indexes = np.empty((entry_count, 2))

        for ind, entry in enumerate(database.analysed_audio):
            length = entry.samps_to_ms(entry.frames)
            hop_size = grain_length / overlap
            grain_indexes[ind][0] = int(length / hop_size) - 1
        grain_indexes[:, 1] = np.cumsum(grain_indexes[:, 0])
        grain_indexes[:, 0] = grain_indexes[:, 1] - grain_indexes[:, 0]
        return grain_indexes

    def brute_force_matcher(self, grain_size, overlap):
        # Source database = musical samples database
        # Target database = Human samples database

        # Count grains of the source database
        source_sample_indexes = self.count_grains(self.source_db, grain_size, overlap)
        try:
            self.output_db.data.create_group("match")
        except ValueError:
            self.logger.debug("Match group already exists in the {0} HDF5 file.".format(self.output_db))

        if self.rematch:
            self.output_db.data["match"].clear()
        #
        final_match_indexes = []

        if self.config:
            weightings = self.config.matcher_weightings
        else:
            weightings = {x: 1. for x in self.matcher_analyses}

        for tind, target_entry in enumerate(self.target_db.analysed_audio):
            # Create an array of grain times for target sample
            target_times = target_entry.generate_grain_times(grain_size, overlap)

            # Stores an accumulated distance between source and target grains,
            # added to by each analysis.
            distance_accum = np.zeros((target_times.shape[0], source_sample_indexes[-1][-1]))
            for analysis in self.matcher_analyses:
                #if not analysis == 'f0':
                    #continue
                self.logger.debug("Current analysis: {0}".format(analysis))
                analysis_formatting = self.analysis_dict[analysis]
                # Get the analysis object for the current entry
                analysis_object = target_entry.analyses[analysis]


                # Get data for all target grains for each analysis
                target_data = target_entry.analysis_data_grains(target_times, analysis)

                # Format the target data ready for matching using the analysis
                # objects match formatting function.
                target_data = analysis_object.formatters[analysis_formatting](target_data)

                data_distance = np.zeros((target_data.shape[0], source_sample_indexes[-1][-1]))

                for sind, source_entry in enumerate(self.source_db.analysed_audio):

                    # Get the start and end array indexes allocated for the
                    # current entry's grains.
                    start_index, end_index = source_sample_indexes[sind]

                    # Create an array of grain times for source sample
                    source_times = source_entry.generate_grain_times(grain_size, overlap)
                    self.logger.debug("Matching \"{0}\" for: {1} to {2}".format(analysis, source_entry.name, target_entry.name))

                    # Get data for all source grains for each analysis
                    source_data = source_entry.analysis_data_grains(source_times, analysis)

                    # Format the source data ready for matching using the analysis
                    # objects match formatting function.
                    source_data = analysis_object.formatters[analysis_formatting](source_data)

                    # Calculate the euclidean distance between the source and
                    # source values of each grain and add to array
                    data_distance[:, start_index:end_index] = np.sqrt((np.vstack(target_data) - source_data)**2)

                # Normalize and weight the distances. A higher weighting gives
                # an analysis presedence over others.
                data_distance *= (1/data_distance.max()) * weightings[analysis]
                distance_accum += data_distance
            match_indexes = distance_accum.argsort(axis=1)[:, :self.match_quantity]

            match_grain_inds = self.calculate_db_inds(match_indexes, source_sample_indexes)
            # Generate the path to the data group that will store the match
            # data in the HDF5 file.
            datafile_path = ''.join(("match/", target_entry.name))

            try:
                self.output_db.data[datafile_path] = match_grain_inds
                self.output_db.data[datafile_path].attrs["grain_size"] = grain_size
                self.output_db.data[datafile_path].attrs["overlap"] = overlap

            except RuntimeError as err:
                raise RuntimeError("Match data couldn't be written to HDF5 "
                                   "file.\n Match data may already exist in the "
                                   "file.\n Try running with the '--rematch' flag "
                                   "to overwrite this data.\n Original error: "
                                   "{0}".format(err))
        return match_grain_inds




    def calculate_db_inds(self, match_indexes, source_sample_indexes):
        """
        Generate the database sample index and grain index for each match based
        on their indexes generated from the concatenated matching

        Output array will be a 3 dimensional array with an axis for each target
        grain, a dimension for each match of said grain and a dimension
        containing database sample index and the sample's grain index.
        """
        mi_shape = match_indexes.shape
        x = match_indexes.flatten()
        x = np.logical_and(
            np.vstack(x)>=source_sample_indexes[:,0],
            np.vstack(x)<=source_sample_indexes[:,1]
        )
        x = x.reshape(mi_shape[0], mi_shape[1], x.shape[1])
        x = np.argmax(x, axis=2)

        # Calculate sample index in database
        match_start_inds = source_sample_indexes[x.flatten(), 0].reshape(mi_shape)
        # Calculate grain index offset from the start of the sample
        match_grain_inds = match_indexes.reshape(mi_shape) - match_start_inds

        return np.dstack((x, match_grain_inds))

    def swap_databases(self):
        """Convenience method to swap databases, changing the source database into the target and vice-versa"""
        self.source_db, self.target_db = self.target_db, self.source_db


class Synthesizer:

    """An object used for synthesizing output based on grain matching."""

    def __init__(self, database1, database2, *args, **kwargs):
        """Initialize synthesizer instance"""
        self.match_db = database1
        self.output_db = database2
        self.config = kwargs.pop("config", None)

    def synthesize(self, grain_size, overlap):
        """Takes a 3D array containing the sample and grain indexes for each grain to be synthesized"""
        jobs = [(i, self.output_db.data["match"][i]) for i in self.output_db.data["match"]]

        for name, job in jobs:
            # Generate output file name/path
            filename, extension = os.path.splitext(name)
            output_name = ''.join((filename, '_output', extension))
            output_path = os.path.join(self.output_db.subdirs["audio"], output_name)
            # Create audio file to save output to.
            output_config = self.config.output_file
            grain_matches = self.output_db.data["match"][name]
            # Get the grain size and overlap used for analysis.
            match_grain_size = grain_matches.attrs["grain_size"]
            match_overlap = grain_matches.attrs["overlap"]

            _grain_size = grain_size
            with AudioFile(
                output_path,
                "w",
                samplerate=output_config["samplerate"],
                format=output_config["format"],
                channels=output_config["channels"]
            ) as output:
                hop_size = (grain_size / overlap) * output.samplerate/1000
                _grain_size *= output.samplerate / 1000
                output_frames = np.zeros(_grain_size + (hop_size*len(grain_matches)-1))
                offset = 0
                for matches in grain_matches:
                    # If there are multiple matches, choose a match at random
                    # from available matches.
                    match_index = np.random.randint(matches.shape[0])
                    final_match = matches[0]
                    with self.match_db.analysed_audio[int(final_match[0])] as match_sample:
                        match_sample.generate_grain_times(match_grain_size, match_overlap)
                        match_grain = match_sample[int(final_match[1])-1]
                        match_grain *= np.hanning(match_grain.size)
                        output_frames[offset:offset+match_grain.size] += match_grain
                    offset += hop_size
                output.write_frames(output_frames)

        pdb.set_trace()

    def swap_databases(self):
        """Convenience method to swap databases, changing the source database into the target and vice-versa"""
        self.match_db, self.output_db = self.output_db, self.match_db


class OrderedSet(collections.MutableSet):
    '''
    Defines a set object that remembers the order that items are added to it.

    Taken from: http://code.activestate.com/recipes/576694/
    '''

    def __init__(self, iterable=None):
        self.end = end = []
        end += [None, end, end]         # sentinel node for doubly linked list
        self.map = {}                   # key --> [key, prev, next]
        if iterable is not None:
            self |= iterable

    def __len__(self):
        return len(self.map)

    def __contains__(self, key):
        return key in self.map

    def add(self, key):
        if key not in self.map:
            end = self.end
            curr = end[1]
            curr[2] = end[1] = self.map[key] = [key, curr, end]

    def discard(self, key):
        if key in self.map:
            key, prev, next = self.map.pop(key)
            prev[2] = next
            next[1] = prev

    def __iter__(self):
        end = self.end
        curr = end[2]
        while curr is not end:
            yield curr[0]
            curr = curr[2]

    def __reversed__(self):
        end = self.end
        curr = end[1]
        while curr is not end:
            yield curr[0]
            curr = curr[1]

    def pop(self, last=True):
        if not self:
            raise KeyError('set is empty')
        key = self.end[1][0] if last else self.end[2][0]
        self.discard(key)
        return key

    def __repr__(self):
        if not self:
            return '%s()' % (self.__class__.__name__,)
        return '%s(%r)' % (self.__class__.__name__, list(self))

    def __eq__(self, other):
        if isinstance(other, OrderedSet):
            return len(self) == len(other) and list(self) == list(other)
        return set(self) == set(other)