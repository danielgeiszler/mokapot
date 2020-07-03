"""
One of the primary purposes of mokapot is to assign confidence estimates to PSMs.
This task is accomplished by ranking PSMs according to a score or metric and
using an appropriate confidence estimation procedure for the type of data
(currently, linear and crosslinked PSMs are supported). In either case,
mokapot can provide confidence estimates based any score, regardless of
whether it was the result of a learned :py:func:`~mokapot.model.Model`
instance or provided independently.

The following classes store the confidence estimates for a dataset based on the
provided score. In either case, they provide utilities to access, save, and
plot these estimates for the various relevant levels (i.e. PSMs, peptides, and
proteins). The :py:func:`LinearConfidence` class is appropriate for most
proteomics datasets, whereas the :py:func:`CrossLinkedConfidence` is
specifically designed for crosslinked peptides.
"""
import os
import logging

import pandas as pd
import matplotlib.pyplot as plt
from triqler import qvality

from . import qvalues

LOGGER = logging.getLogger(__name__)


# Classes ---------------------------------------------------------------------
class Confidence():
    """
    Estimate and store the statistical confidence for a collection of
    PSMs.

    :meta private:
    """
    _level_labs = {"psms": "PSMs",
                   "peptides": "Peptides",
                   "proteins": "Proteins",
                   "csms": "Cross-Linked PSMs",
                   "peptide_pairs": "Peptide Pairs"}

    def __init__(self, psms, scores):
        """
        Initialize a PsmConfidence object.
        """
        self._data = psms.metadata
        self._data[len(psms.columns)] = scores
        self._score_column = self._data.columns[-1]

        # This attribute holds the results as DataFrames:
        self._confidence_estimates = {}

    def __getattr__(self, attr):
        try:
            return self._confidence_estimates[attr]
        except KeyError:
            raise AttributeError

    @property
    def levels(self):
        """
        The available levels for confidence estimates (i.e. PSMs,
        peptides, proteins)
        """
        return list(self._confidence_estimates.keys())

    def to_txt(self, dest_dir=None, file_root=None, sep="\t"):
        """
        Save confidence estimates delimited text files.

        Parameters
        ----------
        dest_dir : str or None, optional
            The directory in which to save the files. The default is the
            current working directory.
        file_root : str or None, optional
            An optional prefix for the confidence estimate files.
        sep : str
            The delimiter to use.

        Returns
        -------
        list of str
            The paths to the saved files.
        """
        file_base = "mokapot"
        if file_root is not None:
            file_base = file_root + "." + file_base
        if dest_dir is not None:
            file_base = os.path.join(dest_dir, file_base)

        out_files = []
        for level, qvals in self._confidence_estimates.items():
            out_file = file_base + f".{level}.txt"
            qvals.to_csv(out_file, sep=sep, index=False)
            out_files.append(out_file)

        return out_files

    def _perform_tdc(self, psm_columns):
        """
        Perform target-decoy competition.

        Parameters
        ----------
        psm_columns : str or list of str
            The columns that define a PSM.
        """
        psm_idx = _groupby_max(self._data, psm_columns, self._score_column)
        self._data = self._data.loc[psm_idx]

    def plot_qvalues(self, level, threshold=0.1, ax=None, **kwargs):
        """
        Plot the accepted number of PSMs, peptides, etc over
        a range of q-values.

        Parameters
        ----------
        level : str, optional
            The level of q-values to report.
        threshold : float, optional
            Indicates the maximum q-value to plot.
        ax : matplotlib.pyplot.Axes, optional
            The matplotlib Axes on which to plot. If `None` the current
            Axes instance is used.
        **kwargs : dict, optional
            Arguments passed to matplotlib.pyplot.plot()

        Returns
        -------
        matplotlib.pyplot.Axes
            A plot of the cumulative number of accepted target PSMs,
            peptides, or proteins.
        """

        if ax is None:
            ax = plt.gca()
        elif not isinstance(ax, plt.Axes):
            raise ValueError("'ax' must be a matplotlib Axes instance.")

        # Calculate cumulative targets at each q-value
        qvals = self._confidence_estimates[level].loc[:, ["mokapot q-value"]]
        qvals = qvals.sort_values(by="mokapot q-value", ascending=True)
        qvals["target"] = 1
        qvals["num"] = qvals["target"].cumsum()
        qvals = qvals.groupby(["mokapot q-value"]).max().reset_index()
        qvals = qvals[["mokapot q-value", "num"]]

        zero = pd.DataFrame({"mokapot q-value": qvals["mokapot q-value"][0],
                             "num": 0}, index=[-1])
        qvals = pd.concat([zero, qvals], sort=True).reset_index(drop=True)

        xmargin = threshold * 0.05
        ymax = qvals.num[qvals["mokapot q-value"] <= (threshold + xmargin)].max()
        ymargin = ymax * 0.05

        # Set margins
        curr_ylims = ax.get_ylim()
        if curr_ylims[1] < ymax + ymargin:
            ax.set_ylim(0 - ymargin, ymax + ymargin)

        ax.set_xlim(0 - xmargin, threshold + xmargin)
        ax.set_xlabel("q-value")
        ax.set_ylabel(f"Accepted {self._level_labs[level]}")

        return ax.step(qvals["mokapot q-value"].values,
                       qvals.num.values, where="post", **kwargs)


class LinearConfidence(Confidence):
    """
    Assign confidence estimates to a set of PSMs

    Estimate q-values and posterior error probabilities (PEPs) for PSMs
    and peptides when ranked by the provided scores.

    Parameters
    ----------
    psms : LinearPsmDataset object
        A collection of PSMs.
    scores : np.ndarray
        A vector containing the score of each PSM.
    desc : bool
        Are higher scores better?

    Attributes
    ----------
    psms : pandas.DataFrame
        Confidence estimates for PSMs in the dataset.
    peptides : pandas.DataFrame
        Confidence estimates for peptide in the dataset.
    """
    def __init__(self, psms, scores, desc=True):
        """Initialize a a LinearPsmConfidence object"""
        LOGGER.info("=== Assigning Confidence ===")
        super().__init__(psms, scores)
        self._data[len(self._data.columns)] = psms.targets
        self._target_column = self._data.columns[-1]
        self._psm_columns = psms._spectrum_columns
        self._peptide_columns = psms._peptide_columns

        LOGGER.info("Performing target-decoy competition...")
        LOGGER.info("Keeping the best match per %s columns...",
                    "+".join(self._psm_columns))

        self._perform_tdc(self._psm_columns)
        LOGGER.info("  - Found %i PSMs from unique spectra.",
                    len(self._data))

        self._assign_confidence(desc=desc)

    def _assign_confidence(self, desc=True):
        """
        Assign confidence to PSMs and peptides.

        Parameters
        ----------
        desc : bool
            Are higher scores better?
        """
        peptide_idx = _groupby_max(self._data, self._peptide_columns,
                                   self._score_column)

        peptides = self._data.loc[peptide_idx]
        LOGGER.info("  - Found %i unique peptides.", len(peptides))

        for level, data in zip(("psms", "peptides"), (self._data, peptides)):
            scores = data.loc[:, self._score_column].values
            targets = data.loc[:, self._target_column].astype(bool).values

            LOGGER.info("Assiging q-values to %s.", self._level_labs[level])
            data["mokapot q-value"] = qvalues.tdc(scores, targets, desc)

            data = data.loc[targets, :] \
                       .sort_values(self._score_column, ascending=(not desc)) \
                       .reset_index(drop=True) \
                       .drop(self._target_column, axis=1) \
                       .rename(columns={self._score_column: "mokapot score"})

            LOGGER.info("Assiging PEPs to %s.", self._level_labs[level])
            _, pep = qvality.getQvaluesFromScores(scores[targets],
                                                  scores[~targets])
            data["mokapot PEP"] = pep
            self._confidence_estimates[level] = data


class CrossLinkedConfidence(Confidence):
    """
    Assign confidence estimates to a set of cross-linked PSMs

    Estimate q-values and posterior error probabilities (PEPs) for
    cross-linked PSMs (CSMs) and the peptide pairs when ranked by the
    provided scores.

    Parameters
    ----------
    psms : CrossLinkedPsmDataset object
        A collection of cross-linked PSMs.
    scores : np.ndarray
        A vector containing the score of each PSM.
    desc : bool
        Are higher scores better?

    Attributes
    ----------
    csms : pandas.DataFrame
        Confidence estimates for cross-linked PSMs in the dataset.
    peptide_pairs : pandas.DataFrame
        Confidence estimates for peptide pairs in the dataset.
    """
    def __init__(self, psms, scores, desc=True):
        """Initialize a CrossLinkedConfidence object"""
        super().__init__(psms, scores)
        self._data[len(self._data.columns)] = psms.targets
        self._target_column = self._data.columns[-1]
        self._psm_columns = psms._spectrum_columns
        self._peptide_columns = psms._peptide_columns

        self._perform_tdc(self._psm_columns)
        self._assign_confidence(desc=desc)

    def _assign_confidence(self, desc=True):
        """
        Assign confidence to PSMs and peptides.

        Parameters
        ----------
        desc : bool
            Are higher scores better?
        """
        peptide_idx = _groupby_max(self._data, self._peptide_columns,
                                   self._score_column)

        peptides = self._data.loc[peptide_idx]
        levels = ("csms", "peptide_pairs")

        for level, data in zip(levels, (self._data, peptides)):
            scores = data.loc[:, self._score_column].values
            targets = data.loc[:, self._target_column].astype(bool).values
            data["mokapot q-value"] = qvalues.crosslink_tdc(scores, targets,
                                                            desc)

            data = data.loc[targets, :] \
                       .sort_values(self._score_column, ascending=(not desc)) \
                       .reset_index(drop=True) \
                       .drop(self._target_column, axis=1) \
                       .rename(columns={self._score_column: "mokapot score"})

            _, pep = qvality.getQvaluesFromScores(scores[targets == 2],
                                                  scores[~targets])
            data["mokapot PEP"] = pep
            self._confidence_estimates[level] = data


# Functions -------------------------------------------------------------------
def _groupby_max(df, by_cols, max_col):
    """Quickly get the indices for the maximum value of col"""
    idx = df.sample(frac=1) \
            .sort_values(list(by_cols)+[max_col], axis=0) \
            .drop_duplicates(list(by_cols), keep="last") \
            .index

    return idx
