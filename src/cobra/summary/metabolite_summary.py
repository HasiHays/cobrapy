"""Provide the metabolite summary class."""


import logging
from operator import attrgetter
from textwrap import shorten
from typing import TYPE_CHECKING, List, Optional, Union

from pandas import DataFrame

from cobra.flux_analysis import flux_variability_analysis, pfba
from cobra.flux_analysis.helpers import normalize_cutoff
from cobra.summary import Summary


if TYPE_CHECKING:
    from cobra.core import Metabolite, Model, Reaction, Solution


logger = logging.getLogger(__name__)


class MetaboliteSummary(Summary):
    """
    Define the metabolite summary.

    Attributes
    ----------
    metabolite: cobra.Metabolite
        The metabolite to summarize.

    See Also
    --------
    Summary : Parent that defines further attributes.
    ReactionSummary
    ModelSummary

    """

    def __init__(
        self,
        *,
        metabolite: "Metabolite",
        model: "Model",
        solution: Optional["Solution"] = None,
        fva: Optional[Union[float, "DataFrame"]] = None,
        **kwargs,
    ):
        """
        Initialize a metabolite summary.

        Parameters
        ----------
        metabolite: cobra.Metabolite
            The metabolite object whose summary we intend to get.
        model : cobra.Model
            The metabolic model for which to generate a metabolite summary.

        Other Parameters
        ----------------
        kwargs :
            Further keyword arguments are passed on to the parent class.

        See Also
        --------
        Summary : Parent that has further default parameters.
        ReactionSummary
        ModelSummary

        """
        super(MetaboliteSummary, self).__init__(model=model, **kwargs)
        self._metabolite = metabolite.copy()
        self._reactions: List["Reaction"] = [
            r.copy() for r in sorted(metabolite.reactions, key=attrgetter("id"))
        ]
        self.producing_fluxes: Optional[DataFrame] = None
        self.consuming_fluxes: Optional[DataFrame] = None
        self._generate(model, solution, fva)

    def _generate(
        self,
        model: "Model",
        solution: Optional["Solution"] = None,
        fva: Optional[Union[float, "DataFrame"]] = None,
    ):
        """"""
        if solution is None:
            logger.info("Generating new parsimonious flux distribution.")
            solution = pfba(model)

        if isinstance(fva, float):
            logger.info("Performing flux variability analysis.")
            fva = flux_variability_analysis(
                model=model,
                reaction_list=[r.id for r in self._reactions],
                fraction_of_optimum=fva,
            )

        # Create the basic flux table.
        flux = DataFrame(
            data=[
                (r.id, solution[r.id], r.get_coefficient(self._metabolite.id),)
                for r in self._reactions
            ],
            columns=["reaction", "flux", "factor"],
            index=[r.id for r in self._reactions],
        )
        # Scale fluxes by stoichiometric coefficient.
        flux["flux"] *= flux["factor"]

        if fva is not None:
            flux = flux.join(fva)
            view = flux[["flux", "minimum", "maximum"]]
            # Set fluxes below model tolerance to zero.
            flux[["flux", "minimum", "maximum"]] = view.where(
                view.abs() >= model.tolerance, 0
            )
            # Create the scaled compound flux.
            flux[["minimum", "maximum"]] = flux[["minimum", "maximum"]].mul(
                flux["factor"], axis=0
            )
            # Negative factors invert the minimum/maximum relationship.
            negative = flux["factor"] < 0
            tmp = flux.loc[negative, "maximum"]
            flux.loc[negative, "maximum"] = flux.loc[negative, "minimum"]
            flux.loc[negative, "minimum"] = tmp
            # Add zero to turn negative zero into positive zero for nicer display later.
            flux[["flux", "minimum", "maximum"]] += 0
        else:
            # Set fluxes below model tolerance to zero.
            flux.loc[flux["flux"].abs() < model.tolerance, "flux"] = 0
            # Add zero to turn negative zero into positive zero for nicer display later.
            flux["flux"] += 0

        # Create production table from producing fluxes or zero fluxes where the
        # metabolite is a product in the reaction.
        is_produced = (flux["flux"] > 0) | ((flux["flux"] == 0) & (flux["factor"] > 0))
        if fva is not None:
            self.producing_fluxes = flux.loc[
                is_produced, ["flux", "minimum", "maximum", "reaction"]
            ].copy()
        else:
            self.producing_fluxes = flux.loc[is_produced, ["flux", "reaction"]].copy()
        production = self.producing_fluxes["flux"].abs()
        self.producing_fluxes["percent"] = production / production.sum()

        # Create consumption table from consuming fluxes or zero fluxes where the
        # metabolite is a substrate in the reaction.
        is_consumed = (flux["flux"] < 0) | ((flux["flux"] == 0) & (flux["factor"] < 0))
        if fva is not None:
            self.consuming_fluxes = flux.loc[
                is_consumed, ["flux", "minimum", "maximum", "reaction"]
            ].copy()
        else:
            self.consuming_fluxes = flux.loc[is_consumed, ["flux", "reaction"]].copy()
        consumption = self.consuming_fluxes["flux"].abs()
        self.consuming_fluxes["percent"] = consumption / consumption.sum()

        self._flux = flux

    def _display_flux(self, frame: DataFrame, names: bool, threshold: float):
        if "minimum" in frame.columns and "maximum" in frame.columns:
            frame = frame.loc[
                (frame["flux"].abs() >= threshold)
                | (frame["minimum"].abs() >= threshold)
                | (frame["maximum"].abs() >= threshold),
                :,
            ].copy()
        else:
            frame = frame.loc[frame["flux"].abs() >= threshold, :].copy()
        reactions = {r.id: r for r in self._reactions}
        frame["definition"] = [
            reactions[rxn_id].build_reaction_string(names)
            for rxn_id in frame["reaction"]
        ]
        if "minimum" in frame.columns and "maximum" in frame.columns:
            frame["range"] = list(
                frame[["minimum", "maximum"]].itertuples(index=False, name=None)
            )
            return frame[["percent", "flux", "range", "reaction", "definition"]]
        else:
            return frame[["percent", "flux", "reaction", "definition"]]

    @staticmethod
    def _string_table(frame: DataFrame, float_format: str, column_width: int):
        frame.columns = [header.title() for header in frame.columns]
        return frame.to_string(
            header=True,
            index=False,
            na_rep="",
            formatters={
                "Percent": "{:.2%}".format,
                "Flux": f"{{:{float_format}}}".format,
                "Range": lambda pair: f"[{pair[0]:{float_format}}; {pair[1]:{float_format}}]",
            },
            max_colwidth=column_width,
        )

    @staticmethod
    def _html_table(frame: DataFrame, float_format: str):
        frame.columns = [header.title() for header in frame.columns]
        return frame.to_html(
            header=True,
            index=False,
            na_rep="",
            formatters={
                "Percent": "{:.2%}".format,
                "Flux": f"{{:{float_format}}}".format,
                "Range": lambda pair: f"[{pair[0]:{float_format}}; {pair[1]:{float_format}}]",
            },
        )

    def to_string(
        self,
        names: bool = False,
        threshold: float = 1e-6,
        float_format: str = ".4G",
        column_width: int = 79,
    ) -> str:
        threshold = normalize_cutoff(self._model, threshold)
        if names:
            metabolite = shorten(
                self._metabolite.name, width=column_width, placeholder="..."
            )
        else:
            metabolite = shorten(
                self._metabolite.id, width=column_width, placeholder="..."
            )

        production = self._string_table(
            self._display_flux(self.producing_fluxes, names, threshold),
            float_format,
            column_width,
        )

        consumption = self._string_table(
            self._display_flux(self.consuming_fluxes, names, threshold),
            float_format,
            column_width,
        )

        return (
            f"{metabolite}\n"
            f"{'=' * len(metabolite)}\n"
            f"Formula: {self._metabolite.formula}\n\n"
            f"Producing Reactions\n"
            f"-------------------\n"
            f"{production}\n\n"
            f"Consuming Reactions\n"
            f"-------------------\n"
            f"{consumption}"
        )

    def to_html(
        self, names: bool = False, threshold: float = 1e-6, float_format: str = ".4G"
    ) -> str:
        if names:
            metabolite = self._metabolite.name
        else:
            metabolite = self._metabolite.id

        production = self._html_table(
            self._display_flux(self.producing_fluxes, names, threshold), float_format,
        )

        consumption = self._html_table(
            self._display_flux(self.consuming_fluxes, names, threshold), float_format,
        )

        return (
            f"<h3>{metabolite}</h3>"
            f"<p>{self._metabolite.formula}</p>"
            f"<h4>Producing Reactions</h4>"
            f"{production}"
            f"<h4>Consuming Reactions</h4>"
            f"{consumption}"
        )
