from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List


BASELINE = {
    "fx_index": 100.0,
    "gov_spending": 20.0,
    "vat_rate": 18.0,
    "public_employment": 12.0,
    "price_index": 100.0,
    "inflation": 4.0,
    "unemployment": 8.0,
    "consumption": 100.0,
    "exports": 20.0,
    "imports": 20.0,
}


def default_settings() -> Dict[str, Any]:
    return {
        "limits": {
            "fx_delta_abs": 4.0,
            "gov_delta_abs": 6.0,
            "vat_delta_abs": 5.0,
            "public_emp_delta_abs": 20.0,
            "tariff_delta_abs": 2.0,
        },
        "thresholds": {
            "yellow_inflation": 12.0,
            "red_inflation": 18.0,
            "yellow_deficit": 8.0,
            "red_deficit": 15.0,
            "red_spending_cut_required": -4.0,
        },
        "initial": dict(BASELINE),
        "engine": {
            # Inflación
            "inflation_persistence": 0.35,
            "inflation_constant": 2.40,
            "inflation_demand_coeff": 0.22,
            "inflation_vat_coeff": 0.38,
            "inflation_fx_coeff": 0.32,
            "inflation_foreign_coeff": 0.05,
            "inflation_tariff_coeff": 0.08,
            # Comercio
            "export_foreign_demand_coeff": 0.35,
            "export_q_coeff": 0.75,
            "export_tariffs_against_coeff": 0.60,
            "import_demand_coeff": 0.40,
            "import_q_coeff": 0.65,
            "import_tariff_coeff": 0.70,
            "import_consumption_gap_coeff": 0.10,
            # Actividad y desempleo
            "activity_demand_coeff": 0.45,
            "activity_trade_coeff": 0.25,
            "activity_import_drag_coeff": 0.15,
            "activity_inflation_drag_coeff": 0.08,
            "activity_public_emp_level_coeff": 0.04,
            "activity_constant": 0.32,
            "unemployment_activity_coeff": 0.20,
            "unemployment_public_emp_coeff": 0.05,
            "unemployment_high_inflation_penalty": 0.04,
            # Consumo
            "consumption_unemployment_coeff": 0.55,
            "consumption_inflation_coeff": 0.35,
            "consumption_vat_coeff": 0.25,
            "consumption_demand_coeff": 0.30,
            "consumption_exports_coeff": 0.10,
            "consumption_constant": 1.40,
            # Fiscal
            "fiscal_gov_weight": 0.75,
            "fiscal_public_emp_weight": 0.40,
        },
    }


@dataclass
class CountryState:
    country_id: int
    name: str
    fx_index: float
    gov_spending: float
    vat_rate: float
    public_employment: float
    price_index: float
    inflation: float
    unemployment: float
    consumption: float
    exports: float
    imports: float
    political_support: float
    deficit_ratio: float
    card_status: str
    required_gov_delta_next: float | None


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def average(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _real_exchange_competitiveness(
    country_id: int,
    fx_levels: Dict[int, float],
    price_levels: Dict[int, float],
) -> float:
    peers = [cid for cid in fx_levels if cid != country_id]
    if not peers:
        return 1.0
    vals = []
    for peer_id in peers:
        q_ij = (fx_levels[country_id] / fx_levels[peer_id]) * (
            price_levels[peer_id] / price_levels[country_id]
        )
        vals.append(q_ij)
    return average(vals)


def _support_from_state(consumption: float, inflation: float, unemployment: float) -> float:
    # Normalización para que el punto de partida ronde 100.
    consumption_score = clamp(consumption, 0.0, 150.0)
    inflation_score = clamp(120.0 - 4.0 * abs(inflation), 0.0, 150.0)
    employment_score = clamp(120.0 - 3.0 * unemployment, 0.0, 150.0)
    return round((consumption_score + inflation_score + employment_score) / 3.0, 2)


def _card_status(inflation: float, deficit_ratio: float, thresholds: Dict[str, float]) -> str:
    if inflation > thresholds["red_inflation"] or deficit_ratio > thresholds["red_deficit"]:
        return "roja"
    if inflation > thresholds["yellow_inflation"] or deficit_ratio > thresholds["yellow_deficit"]:
        return "amarilla"
    return "ninguna"


def compute_next_period(
    countries: List[Dict[str, Any]],
    current_states: Dict[int, Dict[str, Any]],
    tariffs: Dict[int, Dict[int, float]],
    policies: Dict[int, Dict[str, Any]],
    settings: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Calcula el siguiente período usando un motor reducido y oculto.

    countries: lista de países activos
    current_states: estado contemporáneo por país
    tariffs: matriz de aranceles actual por país y socio
    policies: políticas enviadas para el período corriente
    settings: parámetros/umbrales/límites
    """
    engine = settings["engine"]
    thresholds = settings["thresholds"]
    initial = settings["initial"]

    country_ids = [c["id"] for c in countries]

    # 1) Actualizar primero los instrumentos de política que son niveles persistentes.
    fx_levels: Dict[int, float] = {}
    gov_levels: Dict[int, float] = {}
    vat_levels: Dict[int, float] = {}
    public_emp_levels: Dict[int, float] = {}
    new_tariffs: Dict[int, Dict[int, float]] = {cid: {} for cid in country_ids}

    for cid in country_ids:
        prev = current_states[cid]
        pol = policies.get(cid, {})
        fx_delta = float(pol.get("fx_delta", 0.0))
        gov_delta = float(pol.get("gov_delta", 0.0))
        vat_delta = float(pol.get("vat_delta", 0.0))
        public_emp_delta = float(pol.get("public_emp_delta", 0.0))

        fx_levels[cid] = prev["fx_index"] * (1.0 + fx_delta / 100.0)
        gov_levels[cid] = clamp(prev["gov_spending"] * (1.0 + gov_delta / 100.0), 5.0, 80.0)
        vat_levels[cid] = clamp(prev["vat_rate"] + vat_delta, 0.0, 100.0)
        public_emp_levels[cid] = clamp(
            prev["public_employment"] * (1.0 + public_emp_delta / 100.0), 2.0, 80.0
        )

        tariff_changes = pol.get("tariff_changes", {})
        for partner in country_ids:
            if partner == cid:
                continue
            old_t = float(tariffs.get(cid, {}).get(partner, 0.0))
            delta_t = float(tariff_changes.get(partner, 0.0))
            new_tariffs[cid][partner] = clamp(old_t + delta_t, 0.0, 100.0)

    # 2) Variables agregadas auxiliares.
    foreign_inflation = {
        cid: average([current_states[j]["inflation"] for j in country_ids if j != cid])
        for cid in country_ids
    }
    foreign_demand_gap = {}
    avg_tariff_imposed = {}
    avg_tariff_against = {}
    q_gap_pct = {}

    for cid in country_ids:
        peers = [j for j in country_ids if j != cid]
        foreign_demand_gap[cid] = average(
            [
                0.20 * (current_states[j]["consumption"] - initial["consumption"])
                + 0.15 * (gov_levels[j] - initial["gov_spending"])
                for j in peers
            ]
        )
        avg_tariff_imposed[cid] = average([new_tariffs[cid][j] for j in peers])
        avg_tariff_against[cid] = average([new_tariffs[j][cid] for j in peers])
        q = _real_exchange_competitiveness(
            cid,
            fx_levels=fx_levels,
            price_levels={k: current_states[k]["price_index"] for k in country_ids},
        )
        q_gap_pct[cid] = 100.0 * (q - 1.0)

    # 3) Cálculo simultáneo del siguiente estado.
    results: List[Dict[str, Any]] = []
    for country in countries:
        cid = country["id"]
        prev = current_states[cid]
        pol = policies.get(cid, {})
        fx_delta = float(pol.get("fx_delta", 0.0))
        gov_delta = float(pol.get("gov_delta", 0.0))
        vat_delta = float(pol.get("vat_delta", 0.0))
        public_emp_delta = float(pol.get("public_emp_delta", 0.0))

        demand_impulse = (
            0.55 * gov_delta + 0.18 * public_emp_delta - 0.35 * vat_delta
        )

        exp_growth_pct = (
            engine["export_foreign_demand_coeff"] * foreign_demand_gap[cid]
            + engine["export_q_coeff"] * q_gap_pct[cid]
            - engine["export_tariffs_against_coeff"] * avg_tariff_against[cid]
        )
        imp_growth_pct = (
            engine["import_demand_coeff"] * demand_impulse
            - engine["import_q_coeff"] * q_gap_pct[cid]
            - engine["import_tariff_coeff"] * avg_tariff_imposed[cid]
            + engine["import_consumption_gap_coeff"] * (prev["consumption"] - initial["consumption"])
        )

        exports_new = clamp(prev["exports"] * (1.0 + exp_growth_pct / 100.0), 5.0, 100.0)
        imports_new = clamp(prev["imports"] * (1.0 + imp_growth_pct / 100.0), 5.0, 100.0)

        inflation_new = clamp(
            engine["inflation_persistence"] * prev["inflation"]
            + engine["inflation_constant"]
            + engine["inflation_demand_coeff"] * demand_impulse
            + engine["inflation_vat_coeff"] * vat_delta
            + engine["inflation_fx_coeff"] * fx_delta
            + engine["inflation_foreign_coeff"] * foreign_inflation[cid]
            + engine["inflation_tariff_coeff"] * avg_tariff_imposed[cid],
            0.0,
            35.0,
        )
        price_index_new = prev["price_index"] * (1.0 + inflation_new / 100.0)

        activity_growth = (
            engine["activity_demand_coeff"] * demand_impulse
            + engine["activity_trade_coeff"] * exp_growth_pct
            - engine["activity_import_drag_coeff"] * imp_growth_pct
            - engine["activity_inflation_drag_coeff"] * inflation_new
            + engine["activity_public_emp_level_coeff"]
            * (public_emp_levels[cid] - initial["public_employment"])
            + engine["activity_constant"]
        )

        unemployment_new = clamp(
            prev["unemployment"]
            - engine["unemployment_activity_coeff"] * activity_growth
            - engine["unemployment_public_emp_coeff"] * public_emp_delta
            + engine["unemployment_high_inflation_penalty"] * max(inflation_new - 12.0, 0.0),
            0.0,
            25.0,
        )

        unemployment_improvement = prev["unemployment"] - unemployment_new
        consumption_growth = (
            engine["consumption_unemployment_coeff"] * unemployment_improvement
            - engine["consumption_inflation_coeff"] * inflation_new
            - engine["consumption_vat_coeff"] * vat_delta
            + engine["consumption_demand_coeff"] * demand_impulse
            + engine["consumption_exports_coeff"] * exp_growth_pct
            + engine["consumption_constant"]
        )
        consumption_new = clamp(
            prev["consumption"] * (1.0 + consumption_growth / 100.0),
            50.0,
            170.0,
        )

        outlays = (
            engine["fiscal_gov_weight"] * gov_levels[cid]
            + engine["fiscal_public_emp_weight"] * public_emp_levels[cid]
        )
        revenues = (vat_levels[cid] / 100.0) * consumption_new + (
            avg_tariff_imposed[cid] / 100.0
        ) * imports_new
        deficit_ratio = round(clamp(outlays - revenues, -20.0, 40.0), 2)

        support_new = _support_from_state(
            consumption=consumption_new,
            inflation=inflation_new,
            unemployment=unemployment_new,
        )
        card_status = _card_status(inflation_new, deficit_ratio, thresholds)
        next_required_cut = (
            thresholds["red_spending_cut_required"] if card_status == "roja" else None
        )

        results.append(
            {
                "country_id": cid,
                "fx_index": round(fx_levels[cid], 2),
                "gov_spending": round(gov_levels[cid], 2),
                "vat_rate": round(vat_levels[cid], 2),
                "public_employment": round(public_emp_levels[cid], 2),
                "price_index": round(price_index_new, 2),
                "inflation": round(inflation_new, 2),
                "unemployment": round(unemployment_new, 2),
                "consumption": round(consumption_new, 2),
                "exports": round(exports_new, 2),
                "imports": round(imports_new, 2),
                "political_support": round(support_new, 2),
                "deficit_ratio": deficit_ratio,
                "avg_tariff_imposed": round(avg_tariff_imposed[cid], 2),
                "avg_tariff_against": round(avg_tariff_against[cid], 2),
                "q_gap_pct": round(q_gap_pct[cid], 2),
                "card_status": card_status,
                "required_gov_delta_next": next_required_cut,
                "tariffs_out": new_tariffs[cid],
                "applied_policy": {
                    "fx_delta": round(fx_delta, 2),
                    "gov_delta": round(gov_delta, 2),
                    "vat_delta": round(vat_delta, 2),
                    "public_emp_delta": round(public_emp_delta, 2),
                    "tariff_changes": {
                        int(k): round(float(v), 2) for k, v in pol.get("tariff_changes", {}).items()
                    },
                },
            }
        )

    return results
