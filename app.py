from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

import pandas as pd
import plotly.express as px
import streamlit as st

from game_engine import default_settings
from storage import (
    authenticate_user,
    clear_admin_override,
    clear_submission,
    combine_declared_and_override,
    create_world,
    force_finalize,
    get_active_world,
    get_admin_overrides_for_period,
    get_countries,
    get_current_states,
    get_past_policy_table,
    get_period_results,
    get_submissions_for_period,
    get_tariffs,
    impose_required_cut,
    initialize_db,
    maybe_auto_finalize,
    period_submission_status,
    update_world_settings,
    upsert_admin_override,
    upsert_submission,
)

UTC = timezone.utc


# -----------------------------
# Configuración general
# -----------------------------
initialize_db()

st.set_page_config(
    page_title="Demo SIER-like",
    page_icon="🌍",
    layout="wide",
)

if "auth_user" not in st.session_state:
    st.session_state.auth_user = None


# -----------------------------
# Utilidades visuales
# -----------------------------
def current_utc() -> datetime:
    return datetime.now(tz=UTC)



def fmt_timer(seconds_left: int) -> str:
    minutes = max(seconds_left, 0) // 60
    seconds = max(seconds_left, 0) % 60
    return f"{minutes:02d}:{seconds:02d}"



def get_country_name(world: Dict[str, Any], country_id: int) -> str:
    countries = get_countries(int(world["id"]))
    for c in countries:
        if int(c["id"]) == int(country_id):
            return str(c["name"])
    return f"País {country_id}"



def compute_tariff_averages(tariffs: Dict[int, Dict[int, float]]) -> Dict[int, Dict[str, float]]:
    country_ids = list(tariffs.keys())
    results: Dict[int, Dict[str, float]] = {}
    for cid in country_ids:
        partners = [p for p in country_ids if p != cid]
        imposed = [tariffs.get(cid, {}).get(p, 0.0) for p in partners]
        against = [tariffs.get(p, {}).get(cid, 0.0) for p in partners]
        results[cid] = {
            "avg_imposed": round(sum(imposed) / len(imposed), 2) if imposed else 0.0,
            "avg_against": round(sum(against) / len(against), 2) if against else 0.0,
        }
    return results



def build_current_state_df(world: Dict[str, Any]) -> pd.DataFrame:
    states = get_current_states(int(world["id"]))
    tariffs = get_tariffs(int(world["id"]))
    countries = get_countries(int(world["id"]))
    tariff_averages = compute_tariff_averages(tariffs)
    rows: List[Dict[str, Any]] = []
    for c in countries:
        cid = int(c["id"])
        s = states[cid]
        rows.append(
            {
                "País": c["name"],
                "Apoyo político": s["political_support"],
                "Inflación (%)": s["inflation"],
                "Desempleo (%)": s["unemployment"],
                "Consumo": s["consumption"],
                "Gasto": s["gov_spending"],
                "Exportaciones": s["exports"],
                "Importaciones": s["imports"],
                "Tipo de cambio": s["fx_index"],
                "IVA (%)": s["vat_rate"],
                "Empleo público": s["public_employment"],
                "Arancel medio impuesto (%)": tariff_averages.get(cid, {}).get("avg_imposed", 0.0),
                "Arancel medio recibido (%)": tariff_averages.get(cid, {}).get("avg_against", 0.0),
                "Déficit estimado": s["deficit_ratio"],
                "Tarjeta": s["card_status"],
            }
        )
    return pd.DataFrame(rows)



def ranking_df(world: Dict[str, Any]) -> pd.DataFrame:
    df = build_current_state_df(world)
    return df.sort_values("Apoyo político", ascending=False).reset_index(drop=True)


def parse_period_durations(raw: str, total_periods: int, fallback_minutes: int) -> List[int]:
    tokens = [
        tok.strip()
        for tok in str(raw).replace(";", ",").replace("\n", ",").split(",")
        if tok.strip()
    ]
    values: List[int] = []
    for tok in tokens:
        try:
            values.append(max(1, int(float(tok))))
        except Exception:
            continue
    if not values:
        values = [max(1, int(fallback_minutes))]
    if len(values) < int(total_periods):
        values.extend([values[-1]] * (int(total_periods) - len(values)))
    return values[: int(total_periods)]


def durations_to_text(durations: List[int]) -> str:
    return ", ".join(str(int(x)) for x in durations)


def completed_period_for_world(world: Dict[str, Any]) -> int:
    if world["status"] == "finished":
        return int(world["total_periods"])
    return max(int(world["current_period"]) - 1, 0)


def visible_history_limit(world: Dict[str, Any], role: str) -> int:
    if role == "admin":
        if world["status"] == "finished":
            return int(world["total_periods"])
        return int(world["current_period"])
    return completed_period_for_world(world)


def current_submission_signature(world_id: int, period_no: int) -> str:
    submissions = get_submissions_for_period(world_id, period_no)
    normalized: Dict[str, Any] = {}
    for cid, item in submissions.items():
        normalized[str(cid)] = {
            "fx_delta": float(item.get("fx_delta", 0.0)),
            "gov_delta": float(item.get("gov_delta", 0.0)),
            "vat_delta": float(item.get("vat_delta", 0.0)),
            "public_emp_delta": float(item.get("public_emp_delta", 0.0)),
            "tariff_changes": {str(k): float(v) for k, v in item.get("tariff_changes", {}).items()},
            "submitted_at": item.get("submitted_at"),
        }
    return str(normalized)


def render_auto_refresh_probe(world: Dict[str, Any], *, watch_submissions: bool = False) -> None:
    world_id = int(world["id"])
    base_signature = f"{world['status']}|{world['current_period']}|{world['deadline_at']}"
    base_submission_signature = (
        current_submission_signature(world_id, int(world["current_period"]))
        if watch_submissions and world["status"] == "running"
        else ""
    )

    @st.fragment(run_every="5s")
    def _probe() -> None:
        refreshed_world = get_active_world()
        if refreshed_world is None:
            return
        if int(refreshed_world["id"]) != world_id:
            st.rerun()
            return
        new_signature = f"{refreshed_world['status']}|{refreshed_world['current_period']}|{refreshed_world['deadline_at']}"
        if new_signature != base_signature:
            st.rerun()
            return
        if watch_submissions and refreshed_world["status"] == "running":
            new_submission_signature = current_submission_signature(world_id, int(refreshed_world["current_period"]))
            if new_submission_signature != base_submission_signature:
                st.rerun()
                return

    _probe()


def history_df(world: Dict[str, Any]) -> pd.DataFrame:
    results = get_period_results(int(world["id"]))
    rows: List[Dict[str, Any]] = []
    for r in results:
        state = r["state"]
        rows.append(
            {
                "period": int(r["period_no"]),
                "country": r["country"],
                "fx_index": float(state.get("fx_index", 0.0)),
                "gov_spending": float(state.get("gov_spending", 0.0)),
                "vat_rate": float(state.get("vat_rate", 0.0)),
                "public_employment": float(state.get("public_employment", 0.0)),
                "price_index": float(state.get("price_index", 0.0)),
                "inflation": float(state.get("inflation", 0.0)),
                "unemployment": float(state.get("unemployment", 0.0)),
                "consumption": float(state.get("consumption", 0.0)),
                "exports": float(state.get("exports", 0.0)),
                "imports": float(state.get("imports", 0.0)),
                "political_support": float(state.get("political_support", 0.0)),
                "deficit_ratio": float(state.get("deficit_ratio", 0.0)),
                "q_gap_pct": float(state.get("q_gap_pct", 0.0)),
                "avg_tariff_imposed": float(state.get("avg_tariff_imposed", 0.0)),
                "avg_tariff_against": float(state.get("avg_tariff_against", 0.0)),
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        return df

    df = df.sort_values(["country", "period"]).reset_index(drop=True)
    gross_total = df["consumption"] + df["gov_spending"] + df["exports"] + df["imports"]
    gross_total = gross_total.where(gross_total > 0, 1.0)
    df["share_consumption"] = 100.0 * df["consumption"] / gross_total
    df["share_gov"] = 100.0 * df["gov_spending"] / gross_total
    df["share_exports"] = 100.0 * df["exports"] / gross_total
    df["share_imports"] = 100.0 * df["imports"] / gross_total

    for var in ["consumption", "inflation", "unemployment", "political_support"]:
        pct = df.groupby("country")[var].pct_change()
        pct = pct.replace([float("inf"), float("-inf")], 0.0).fillna(0.0) * 100.0
        df[f"{var}_var_pct"] = pct

    employed_total = (100.0 - df["unemployment"]).clip(lower=0.0)
    public_emp_effective = pd.concat([df["public_employment"], employed_total], axis=1).min(axis=1)
    private_emp = (employed_total - public_emp_effective).clip(lower=0.0)
    total_emp = (public_emp_effective + private_emp).replace(0.0, 1.0)
    df["public_emp_share"] = 100.0 * public_emp_effective / total_emp
    df["private_emp_share"] = 100.0 * private_emp / total_emp
    df["real_fx_index"] = 100.0 + df["q_gap_pct"]
    return df


def support_delta_pct(world: Dict[str, Any], country_name: str) -> float:
    df = history_df(world)
    if df.empty:
        return 0.0
    sub = df[df["country"] == country_name].sort_values("period")
    if len(sub) <= 1:
        return 0.0
    return float(sub["political_support_var_pct"].iloc[-1])


def render_graph_tabs(world: Dict[str, Any], *, default_country_name: str | None = None, key_prefix: str = "graphs") -> None:
    df = history_df(world)
    if df.empty:
        st.info("Aún no hay series para graficar.")
        return

    country_names = sorted(df["country"].unique().tolist())
    if default_country_name not in country_names:
        default_country_name = country_names[0]

    tab1, tab2, tab3, tab4, tab5 = st.tabs(
        [
            "Apoyo político",
            "Demanda",
            "Variaciones",
            "Empleo",
            "Tipo de cambio real",
        ]
    )

    with tab1:
        fig = px.line(
            df,
            x="period",
            y="political_support",
            color="country",
            markers=True,
            labels={"period": "Período", "political_support": "Apoyo político", "country": "País"},
            title="Apoyo político por país",
        )
        fig.update_layout(legend_title_text="País")
        st.plotly_chart(fig, width="stretch")

    with tab2:
        selected = st.selectbox(
            "País",
            country_names,
            index=country_names.index(default_country_name),
            key=f"{key_prefix}_demand_country",
        )
        sub = df[df["country"] == selected].copy()
        demand_long = pd.DataFrame(
            {
                "period": list(sub["period"]) * 4,
                "Componente": ["Consumo"] * len(sub)
                + ["Gasto público"] * len(sub)
                + ["Exportaciones"] * len(sub)
                + ["Importaciones (brutas)"] * len(sub),
                "Participación": list(sub["share_consumption"])
                + list(sub["share_gov"])
                + list(sub["share_exports"])
                + list(sub["share_imports"]),
            }
        )
        fig = px.bar(
            demand_long,
            x="period",
            y="Participación",
            color="Componente",
            title=f"Participación de componentes de demanda – {selected}",
            labels={"period": "Período", "Participación": "% del total bruto C+G+X+M"},
        )
        fig.update_layout(barmode="stack")
        st.plotly_chart(fig, width="stretch")
        st.caption("Las importaciones se muestran en términos brutos para que cada barra sume 100%.")

    with tab3:
        selected = st.selectbox(
            "País ",
            country_names,
            index=country_names.index(default_country_name),
            key=f"{key_prefix}_var_country",
        )
        sub = df[df["country"] == selected].copy()
        var_long = pd.DataFrame(
            {
                "period": list(sub["period"]) * 3,
                "Serie": ["Consumo"] * len(sub)
                + ["Inflación"] * len(sub)
                + ["Desempleo"] * len(sub),
                "Variación porcentual": list(sub["consumption_var_pct"])
                + list(sub["inflation_var_pct"])
                + list(sub["unemployment_var_pct"]),
            }
        )
        fig = px.line(
            var_long,
            x="period",
            y="Variación porcentual",
            color="Serie",
            markers=True,
            title=f"Variación porcentual respecto del período anterior – {selected}",
            labels={"period": "Período"},
        )
        st.plotly_chart(fig, width="stretch")

    with tab4:
        selected = st.selectbox(
            "País  ",
            country_names,
            index=country_names.index(default_country_name),
            key=f"{key_prefix}_employment_country",
        )
        sub = df[df["country"] == selected].copy()
        emp_long = pd.DataFrame(
            {
                "period": list(sub["period"]) * 2,
                "Tipo": ["Privado"] * len(sub) + ["Público"] * len(sub),
                "Participación": list(sub["private_emp_share"]) + list(sub["public_emp_share"]),
            }
        )
        fig = px.bar(
            emp_long,
            x="period",
            y="Participación",
            color="Tipo",
            title=f"Composición del empleo – {selected}",
            labels={"period": "Período", "Participación": "% del empleo total"},
        )
        fig.update_layout(barmode="stack")
        st.plotly_chart(fig, width="stretch")

    with tab5:
        fig = px.line(
            df,
            x="period",
            y="real_fx_index",
            color="country",
            markers=True,
            labels={"period": "Período", "real_fx_index": "Índice de tipo de cambio real", "country": "País"},
            title="Tipo de cambio real promedio por país (base 100 = equilibrio)",
        )
        st.plotly_chart(fig, width="stretch")



def render_countdown(world: Dict[str, Any]) -> None:
    world_id = int(world["id"])

    @st.fragment(run_every="1s")
    def _countdown_fragment() -> None:
        refreshed_world = get_active_world()
        if refreshed_world is None or int(refreshed_world["id"]) != world_id:
            st.info("No hay un mundo activo.")
            return

        if refreshed_world["status"] != "running":
            if refreshed_world["status"] == "finished":
                st.success("La partida ya terminó.")
            else:
                st.info(f"Estado del mundo: {refreshed_world['status']}")
            return

        if maybe_auto_finalize(world_id):
            st.rerun()
            return

        deadline_str = refreshed_world["deadline_at"]
        if not deadline_str:
            st.warning("No hay cronómetro activo.")
            return
        deadline = datetime.fromisoformat(deadline_str)
        seconds_left = int((deadline - current_utc()).total_seconds())
        label = fmt_timer(seconds_left)
        if seconds_left <= 10:
            st.error(f"⏱️ Tiempo restante del período {refreshed_world['current_period']}: {label}")
        else:
            st.info(f"⏱️ Tiempo restante del período {refreshed_world['current_period']}: {label}")

    _countdown_fragment()



def login_screen() -> None:
    st.title("🌍 Demo SIER-like")
    st.caption("Versión MVP en Streamlit para demostración docente")

    col1, col2 = st.columns([1.1, 0.9])
    with col1:
        st.markdown(
            """
            Esta demo permite jugar una simulación macroeconómica internacional con:
            
            - cuenta de profesor
            - cuenta líder por país
            - cuenta de visualización por país
            - cronómetro por período
            - políticas simultáneas y transición automática del mundo
            """
        )
        st.warning("Credenciales iniciales del profesor: usuario `admin` y contraseña `admin123`")

    with col2:
        with st.form("login_form", clear_on_submit=False):
            username = st.text_input("Usuario")
            password = st.text_input("Contraseña", type="password")
            submitted = st.form_submit_button("Ingresar", width="stretch")
            if submitted:
                user = authenticate_user(username, password)
                if user is None:
                    st.error("Usuario o contraseña incorrectos.")
                else:
                    st.session_state.auth_user = user
                    st.rerun()


# -----------------------------
# Panel admin
# -----------------------------

def world_creation_panel() -> None:
    st.subheader("Crear o reiniciar mundo")
    defaults = default_settings()

    current_world = get_active_world()
    import json
    base_settings = json.loads(json.dumps(current_world["settings"] if current_world else defaults))

    num_countries = st.number_input(
        "Cantidad de países",
        min_value=2,
        max_value=10,
        value=int(st.session_state.get("setup_num_countries", 4)),
        step=1,
        key="setup_num_countries",
    )

    default_total_periods = int(base_settings.get("total_periods_hint", 4)) if current_world is None else int(current_world["total_periods"])
    default_duration = 15 if current_world is None else int(world_duration := current_world["period_duration_minutes"])
    default_durations = base_settings.get("period_durations", [default_duration] * int(default_total_periods))
    default_durations = parse_period_durations(durations_to_text(default_durations), int(default_total_periods), default_duration)

    with st.form("world_creation_form"):
        col_a, col_b, col_c = st.columns(3)
        with col_a:
            world_name = st.text_input("Nombre del mundo", value="Mundo demo")
            total_periods = st.number_input("Cantidad total de períodos", min_value=1, max_value=20, value=int(default_total_periods), step=1)
        with col_b:
            duration = st.number_input("Minutos base por período", min_value=1, max_value=60, value=int(default_duration), step=1)
            duration_schedule_text = st.text_input(
                "Duraciones por período (coma separada)",
                value=durations_to_text(default_durations),
                help="Ejemplo: 15, 15, 12, 10",
            )
            auto_advance = st.checkbox("Avanzar automáticamente si todos envían", value=False)
        with col_c:
            st.write("")
            st.write("")
            st.info("Crear un mundo nuevo borra la partida y las cuentas de países previas.")

        st.markdown("### Límites institucionales")
        lim1, lim2, lim3, lim4, lim5 = st.columns(5)
        limits = base_settings["limits"]
        with lim1:
            fx_limit = st.number_input("Máx. Δ TC (%)", value=float(limits["fx_delta_abs"]), step=0.5)
        with lim2:
            gov_limit = st.number_input("Máx. Δ gasto (%)", value=float(limits["gov_delta_abs"]), step=0.5)
        with lim3:
            vat_limit = st.number_input("Máx. Δ IVA (pp)", value=float(limits["vat_delta_abs"]), step=0.5)
        with lim4:
            pub_limit = st.number_input("Máx. Δ empleo público (%)", value=float(limits["public_emp_delta_abs"]), step=1.0)
        with lim5:
            tariff_limit = st.number_input("Máx. Δ arancel (pp)", value=float(limits["tariff_delta_abs"]), step=0.5)

        st.markdown("### Tarjetas y disciplina fiscal")
        thr = base_settings["thresholds"]
        th1, th2, th3, th4, th5 = st.columns(5)
        with th1:
            yellow_infl = st.number_input("Amarilla: inflación >", value=float(thr["yellow_inflation"]), step=0.5)
        with th2:
            red_infl = st.number_input("Roja: inflación >", value=float(thr["red_inflation"]), step=0.5)
        with th3:
            yellow_def = st.number_input("Amarilla: déficit >", value=float(thr["yellow_deficit"]), step=0.5)
        with th4:
            red_def = st.number_input("Roja: déficit >", value=float(thr["red_deficit"]), step=0.5)
        with th5:
            required_cut = st.number_input("Recorte obligatorio gasto (%)", value=float(thr["red_spending_cut_required"]), step=0.5)

        st.markdown("### Parámetros del motor")
        eng = base_settings["engine"]
        e1, e2, e3, e4 = st.columns(4)
        with e1:
            infl_p = st.number_input("Persistencia inflación", value=float(eng["inflation_persistence"]), step=0.01, format="%.2f")
            infl_g = st.number_input("Inflación por gasto", value=float(eng["inflation_demand_coeff"]), step=0.01, format="%.2f")
            infl_vat = st.number_input("Inflación por IVA", value=float(eng["inflation_vat_coeff"]), step=0.01, format="%.2f")
            infl_fx = st.number_input("Inflación por TC", value=float(eng["inflation_fx_coeff"]), step=0.01, format="%.2f")
        with e2:
            ex_fd = st.number_input("Exportaciones por demanda externa", value=float(eng["export_foreign_demand_coeff"]), step=0.01, format="%.2f")
            ex_q = st.number_input("Exportaciones por competitividad", value=float(eng["export_q_coeff"]), step=0.01, format="%.2f")
            im_d = st.number_input("Importaciones por demanda", value=float(eng["import_demand_coeff"]), step=0.01, format="%.2f")
            im_q = st.number_input("Importaciones por competitividad", value=float(eng["import_q_coeff"]), step=0.01, format="%.2f")
        with e3:
            unemp_a = st.number_input("Desempleo por actividad", value=float(eng["unemployment_activity_coeff"]), step=0.01, format="%.2f")
            unemp_pe = st.number_input("Desempleo por empleo público", value=float(eng["unemployment_public_emp_coeff"]), step=0.01, format="%.2f")
            cons_u = st.number_input("Consumo por mejora laboral", value=float(eng["consumption_unemployment_coeff"]), step=0.01, format="%.2f")
            cons_infl = st.number_input("Consumo por inflación", value=float(eng["consumption_inflation_coeff"]), step=0.01, format="%.2f")
        with e4:
            fiscal_g = st.number_input("Peso fiscal del gasto", value=float(eng["fiscal_gov_weight"]), step=0.01, format="%.2f")
            fiscal_pe = st.number_input("Peso fiscal empleo público", value=float(eng["fiscal_public_emp_weight"]), step=0.01, format="%.2f")
            infl_const = st.number_input("Constante de inflación", value=float(eng["inflation_constant"]), step=0.01, format="%.2f")
            cons_const = st.number_input("Constante de consumo", value=float(eng["consumption_constant"]), step=0.01, format="%.2f")

        st.markdown("### Países y credenciales")
        country_specs = []
        for i in range(int(num_countries)):
            st.markdown(f"**País {i + 1}**")
            c1, c2, c3 = st.columns(3)
            with c1:
                cname = st.text_input(f"Nombre país {i+1}", value=f"País {i+1}")
            with c2:
                viewer_user = st.text_input(f"Usuario visualización {i+1}", value=f"pais{i+1}")
                viewer_pass = st.text_input(f"Contraseña visualización {i+1}", value=f"pais{i+1}123", type="password")
            with c3:
                leader_user = st.text_input(f"Usuario líder {i+1}", value=f"lider{i+1}")
                leader_pass = st.text_input(f"Contraseña líder {i+1}", value=f"lider{i+1}123", type="password")
            country_specs.append(
                {
                    "name": cname.strip() or f"País {i+1}",
                    "viewer_username": viewer_user.strip() or f"pais{i+1}",
                    "viewer_password": viewer_pass or f"pais{i+1}123",
                    "leader_username": leader_user.strip() or f"lider{i+1}",
                    "leader_password": leader_pass or f"lider{i+1}123",
                }
            )

        create_clicked = st.form_submit_button("Crear / reiniciar mundo", width="stretch")
        if create_clicked:
            durations = parse_period_durations(duration_schedule_text, int(total_periods), int(duration))
            settings = base_settings
            settings["period_durations"] = durations
            settings["limits"].update(
                {
                    "fx_delta_abs": float(fx_limit),
                    "gov_delta_abs": float(gov_limit),
                    "vat_delta_abs": float(vat_limit),
                    "public_emp_delta_abs": float(pub_limit),
                    "tariff_delta_abs": float(tariff_limit),
                }
            )
            settings["thresholds"].update(
                {
                    "yellow_inflation": float(yellow_infl),
                    "red_inflation": float(red_infl),
                    "yellow_deficit": float(yellow_def),
                    "red_deficit": float(red_def),
                    "red_spending_cut_required": float(required_cut),
                }
            )
            settings["engine"].update(
                {
                    "inflation_persistence": float(infl_p),
                    "inflation_demand_coeff": float(infl_g),
                    "inflation_vat_coeff": float(infl_vat),
                    "inflation_fx_coeff": float(infl_fx),
                    "export_foreign_demand_coeff": float(ex_fd),
                    "export_q_coeff": float(ex_q),
                    "import_demand_coeff": float(im_d),
                    "import_q_coeff": float(im_q),
                    "unemployment_activity_coeff": float(unemp_a),
                    "unemployment_public_emp_coeff": float(unemp_pe),
                    "consumption_unemployment_coeff": float(cons_u),
                    "consumption_inflation_coeff": float(cons_infl),
                    "fiscal_gov_weight": float(fiscal_g),
                    "fiscal_public_emp_weight": float(fiscal_pe),
                    "inflation_constant": float(infl_const),
                    "consumption_constant": float(cons_const),
                }
            )
            usernames = [x["viewer_username"] for x in country_specs] + [x["leader_username"] for x in country_specs]
            if len(set(usernames)) != len(usernames):
                st.error("Los usuarios deben ser únicos.")
            else:
                create_world(
                    world_name=world_name,
                    total_periods=int(total_periods),
                    period_duration_minutes=int(durations[0]),
                    auto_advance_when_all_submitted=bool(auto_advance),
                    settings=settings,
                    country_specs=country_specs,
                )
                st.success("Mundo creado correctamente.")
                st.rerun()



def admin_current_world_panel(world: Dict[str, Any]) -> None:
    st.subheader("Control del profesor")
    render_auto_refresh_probe(world, watch_submissions=True)

    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        st.metric("Período actual", str(world["current_period"]))
    with c2:
        st.metric("Total de períodos", str(world["total_periods"]))
    with c3:
        st.metric("Duración período actual (min)", str(world["period_duration_minutes"]))
    with c4:
        st.metric("Estado", str(world["status"]))
    with c5:
        if st.button("Refrescar panel", width="stretch"):
            st.rerun()

    render_countdown(world)

    b1, b2 = st.columns([1, 1])
    with b1:
        if st.button("Terminar período ahora", width="stretch", disabled=world["status"] != "running"):
            if force_finalize(int(world["id"])):
                st.success("Período cerrado y mundo actualizado.")
                st.rerun()
    with b2:
        st.toggle(
            "Avance automático si todos envían",
            key="dummy_auto_toggle",
            value=bool(world["auto_advance_when_all_submitted"]),
            disabled=True,
            help="Se cambia desde el formulario de parámetros de partida debajo.",
        )

    st.markdown("### Estado actual de todos los países")
    st.dataframe(build_current_state_df(world), width="stretch", hide_index=True)

    st.markdown("### Estado de envíos del período")
    st.dataframe(
        pd.DataFrame(period_submission_status(int(world["id"]), int(world["current_period"]))),
        width="stretch",
        hide_index=True,
    )

    st.markdown("### Políticas del período actual en tiempo real")
    current_submission = get_submissions_for_period(int(world["id"]), int(world["current_period"]))
    current_overrides = get_admin_overrides_for_period(int(world["id"]), int(world["current_period"]))
    country_map = {int(c["id"]): c["name"] for c in get_countries(int(world["id"]))}
    live_rows: List[Dict[str, Any]] = []
    status_rows = {
        int(r["country_id"]): r for r in period_submission_status(int(world["id"]), int(world["current_period"]))
    }
    states_now = get_current_states(int(world["id"]))
    for cid, name in country_map.items():
        item = current_submission.get(cid)
        override = current_overrides.get(cid)
        effective = combine_declared_and_override(item, override, required_cut=states_now[cid].get("required_gov_delta_next"))
        player_tariffs_txt = ", ".join(
            f"{country_map.get(int(k), k)}: {float(v):+.1f} pp"
            for k, v in (item or {}).get("tariff_changes", {}).items()
            if abs(float(v)) > 1e-9
        ) or "Sin cambios"
        admin_tariffs_txt = ", ".join(
            f"{country_map.get(int(k), k)}: {float(v):+.1f} pp"
            for k, v in (override or {}).get("tariff_changes", {}).items()
            if abs(float(v)) > 1e-9
        ) or "Sin shock"
        effective_tariffs_txt = ", ".join(
            f"{country_map.get(int(k), k)}: {float(v):+.1f} pp"
            for k, v in effective.get("tariff_changes", {}).items()
            if abs(float(v)) > 1e-9
        ) or "Sin cambios"
        live_rows.append(
            {
                "País": name,
                "Jugador envió": "Sí" if item is not None else "No",
                "Por": status_rows.get(cid, {}).get("submitted_by"),
                "Hora": status_rows.get(cid, {}).get("submitted_at"),
                "Shock admin": "Sí" if override is not None else "No",
                "Δ TC jugador": float((item or {}).get("fx_delta", 0.0)),
                "Δ TC shock": float((override or {}).get("fx_delta", 0.0)),
                "Δ TC efectivo": float(effective.get("fx_delta", 0.0)),
                "Δ gasto jugador": float((item or {}).get("gov_delta", 0.0)),
                "Δ gasto shock": float((override or {}).get("gov_delta", 0.0)),
                "Δ gasto efectivo": float(effective.get("gov_delta", 0.0)),
                "Aranceles jugador": player_tariffs_txt,
                "Aranceles shock": admin_tariffs_txt,
                "Aranceles efectivos": effective_tariffs_txt,
            }
        )
    st.dataframe(pd.DataFrame(live_rows), width="stretch", hide_index=True)

    with st.expander("Editar parámetros del mundo activo"):
        settings = world["settings"]
        existing_durations = parse_period_durations(
            durations_to_text(settings.get("period_durations", [int(world["period_duration_minutes"])] * int(world["total_periods"]))),
            int(world["total_periods"]),
            int(world["period_duration_minutes"]),
        )
        with st.form("update_world_settings_form"):
            col1, col2, col3 = st.columns(3)
            with col1:
                new_total_periods = st.number_input("Total de períodos", min_value=1, max_value=20, value=int(world["total_periods"]), step=1)
                durations_text = st.text_input("Duraciones por período (coma separada)", value=durations_to_text(existing_durations))
                auto_adv = st.checkbox("Avance automático si todos envían", value=bool(world["auto_advance_when_all_submitted"]))
            with col2:
                settings["thresholds"]["yellow_inflation"] = st.number_input("Inflación amarilla", value=float(settings["thresholds"]["yellow_inflation"]), step=0.5)
                settings["thresholds"]["red_inflation"] = st.number_input("Inflación roja", value=float(settings["thresholds"]["red_inflation"]), step=0.5)
                settings["thresholds"]["yellow_deficit"] = st.number_input("Déficit amarillo", value=float(settings["thresholds"]["yellow_deficit"]), step=0.5)
                settings["thresholds"]["red_deficit"] = st.number_input("Déficit rojo", value=float(settings["thresholds"]["red_deficit"]), step=0.5)
            with col3:
                settings["engine"]["inflation_persistence"] = st.number_input("Persistencia inflacionaria", value=float(settings["engine"]["inflation_persistence"]), step=0.01, format="%.2f")
                settings["engine"]["inflation_demand_coeff"] = st.number_input("Inflación por gasto", value=float(settings["engine"]["inflation_demand_coeff"]), step=0.01, format="%.2f")
                settings["engine"]["export_q_coeff"] = st.number_input("Exportaciones por competitividad", value=float(settings["engine"]["export_q_coeff"]), step=0.01, format="%.2f")
                settings["engine"]["unemployment_activity_coeff"] = st.number_input("Desempleo por actividad", value=float(settings["engine"]["unemployment_activity_coeff"]), step=0.01, format="%.2f")
            saved = st.form_submit_button("Guardar parámetros", width="stretch")
            if saved:
                durations = parse_period_durations(durations_text, int(new_total_periods), int(world["period_duration_minutes"]))
                settings["period_durations"] = durations
                update_world_settings(
                    int(world["id"]),
                    settings=settings,
                    total_periods=int(new_total_periods),
                    period_duration_minutes=int(durations[max(min(int(world["current_period"]) - 1, len(durations) - 1), 0)]),
                    auto_advance_when_all_submitted=bool(auto_adv),
                )
                st.success("Parámetros actualizados para los próximos cierres de período.")
                st.rerun()

    with st.expander("Sobrescribir o editar la política de un país en el período actual"):
        countries = get_countries(int(world["id"]))
        country_options = {c["name"]: c["id"] for c in countries}
        selected_country_name = st.selectbox("País a editar", list(country_options.keys()), key="admin_override_country")
        selected_country_id = int(country_options[selected_country_name])
        current_overrides = get_admin_overrides_for_period(int(world["id"]), int(world["current_period"]))
        existing_override = current_overrides.get(selected_country_id, {})
        limits = world["settings"]["limits"]

        with st.form("admin_override_form"):
            col1, col2 = st.columns(2)
            with col1:
                fx_delta = st.number_input("Δ tipo de cambio (%)", value=float(existing_override.get("fx_delta", 0.0)), min_value=-float(limits["fx_delta_abs"]), max_value=float(limits["fx_delta_abs"]), step=0.5)
                gov_delta = st.number_input("Δ gasto (%)", value=float(existing_override.get("gov_delta", 0.0)), min_value=-float(limits["gov_delta_abs"]), max_value=float(limits["gov_delta_abs"]), step=0.5)
            with col2:
                vat_delta = st.number_input("Δ IVA (pp)", value=float(existing_override.get("vat_delta", 0.0)), min_value=-float(limits["vat_delta_abs"]), max_value=float(limits["vat_delta_abs"]), step=0.5)
                public_emp_delta = st.number_input("Δ empleo público (%)", value=float(existing_override.get("public_emp_delta", 0.0)), min_value=-float(limits["public_emp_delta_abs"]), max_value=float(limits["public_emp_delta_abs"]), step=1.0)

            tariff_matrix = get_tariffs(int(world["id"]))
            tariff_changes_existing = existing_override.get("tariff_changes", {})
            tariff_changes: Dict[int, float] = {}
            for c in countries:
                if int(c["id"]) == selected_country_id:
                    continue
                current_tariff = float(tariff_matrix.get(selected_country_id, {}).get(int(c["id"]), 0.0))
                tariff_changes[int(c["id"])] = st.number_input(
                    f"Δ arancel contra {c['name']} (actual: {current_tariff:.1f}%)",
                    value=float(tariff_changes_existing.get(int(c["id"]), 0.0)),
                    min_value=-float(limits["tariff_delta_abs"]),
                    max_value=float(limits["tariff_delta_abs"]),
                    step=0.5,
                    key=f"admin_tariff_{selected_country_id}_{c['id']}"
                )

            save_override = st.form_submit_button("Guardar shock oculto del administrador", width="stretch")
            if save_override:
                final_vat = float(states := get_current_states(int(world["id"]))[selected_country_id]["vat_rate"]) + float(vat_delta)
                invalid = False
                if final_vat < 0 or final_vat > 100:
                    st.error("El IVA final debe quedar entre 0% y 100%.")
                    invalid = True
                for partner_id, delta in tariff_changes.items():
                    current_tariff = float(tariff_matrix.get(selected_country_id, {}).get(int(partner_id), 0.0))
                    final_tariff = current_tariff + float(delta)
                    if final_tariff < 0 or final_tariff > 100:
                        st.error("Un arancel debe quedar entre 0% y 100%.")
                        invalid = True
                        break
                if not invalid:
                    upsert_admin_override(
                        world_id=int(world["id"]),
                        period_no=int(world["current_period"]),
                        country_id=selected_country_id,
                        submitted_by_user_id=int(st.session_state.auth_user["id"]),
                        fx_delta=float(fx_delta),
                        gov_delta=float(gov_delta),
                        vat_delta=float(vat_delta),
                        public_emp_delta=float(public_emp_delta),
                        tariff_changes=tariff_changes,
                    )
                    st.success("Shock oculto del administrador guardado.")
                    st.rerun()

        cols = st.columns([1, 1, 1])
        with cols[0]:
            if st.button("Eliminar envío del jugador", width="stretch"):
                clear_submission(int(world["id"]), int(world["current_period"]), selected_country_id)
                st.success("Envío del jugador eliminado.")
                st.rerun()
        with cols[1]:
            if st.button("Eliminar shock oculto", width="stretch"):
                clear_admin_override(int(world["id"]), int(world["current_period"]), selected_country_id)
                st.success("Shock oculto eliminado.")
                st.rerun()
        with cols[2]:
            states = get_current_states(int(world["id"]))
            required_now = states[selected_country_id]["required_gov_delta_next"]
            force_red = st.button(
                "Imponer recorte obligatorio de gasto en próxima ronda",
                width="stretch",
            )
            if force_red:
                cut = world["settings"]["thresholds"]["red_spending_cut_required"]
                impose_required_cut(selected_country_id, float(cut))
                st.success("Restricción impuesta.")
                st.rerun()
            if required_now is not None:
                st.caption(f"Restricción vigente del país: Δ gasto <= {required_now}%")

    with st.expander("Historial de políticas enviadas"):
        max_period = visible_history_limit(world, "admin")
        policies = get_past_policy_table(int(world["id"]), max_period=max_period)
        if policies:
            st.dataframe(pd.DataFrame(policies), width="stretch", hide_index=True)
        else:
            st.info("Todavía no hay políticas enviadas.")

    with st.expander("Resultados por período"):
        results = get_period_results(int(world["id"]))
        if results:
            rows = []
            for r in results:
                rows.append(
                    {
                        "Período": r["period_no"],
                        "País": r["country"],
                        "Apoyo": r["state"].get("political_support"),
                        "Inflación": r["state"].get("inflation"),
                        "Desempleo": r["state"].get("unemployment"),
                        "Consumo": r["state"].get("consumption"),
                        "Tarjeta": r["state"].get("card_status"),
                        "Política": r["applied_policy"],
                    }
                )
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
        else:
            st.info("Aún no hay resultados guardados.")

    st.markdown("### Gráficos")
    render_graph_tabs(world, default_country_name=get_countries(int(world["id"]))[0]["name"], key_prefix="admin")


# -----------------------------
# Panel país
# -----------------------------

def country_dashboard(user: Dict[str, Any], world: Dict[str, Any]) -> None:
    countries = get_countries(int(world["id"]))
    country_id = int(user["country_id"])
    my_country_name = get_country_name(world, country_id)
    states = get_current_states(int(world["id"]))
    if country_id not in states:
        st.error("Tu sesión pertenece a una partida anterior o tu cuenta ya no está activa en este mundo. Cierra sesión e ingresa nuevamente.")
        return
    my_state = states[country_id]
    tariffs = get_tariffs(int(world["id"]))

    st.title(f"🌍 {my_country_name}")
    role_label = "Líder" if user["role"] == "country_leader" else "Visualización"
    st.caption(f"Rol: {role_label}")

    render_auto_refresh_probe(world, watch_submissions=False)
    render_countdown(world)

    support_delta = support_delta_pct(world, my_country_name)

    top1, top2, top3, top4, top5 = st.columns(5)
    with top1:
        st.metric("Período", str(world["current_period"]))
    with top2:
        st.metric("Apoyo político", f"{my_state['political_support']:.2f}", delta=f"{support_delta:.2f}%")
    with top3:
        st.metric("Tarjeta", my_state["card_status"].capitalize())
    with top4:
        req_cut = my_state["required_gov_delta_next"]
        st.metric("Recorte obligatorio próximo", "No" if req_cut is None else f"Sí ({req_cut}%)")
    with top5:
        if st.button("Refrescar vista", width="stretch"):
            st.rerun()

    st.markdown("### Estado actual")
    st.dataframe(build_current_state_df(world), width="stretch", hide_index=True)

    st.markdown("### Matriz actual de aranceles")
    tariff_rows = []
    for c_from in countries:
        row = {"País emisor": c_from["name"]}
        for c_to in countries:
            if int(c_from["id"]) == int(c_to["id"]):
                continue
            row[c_to["name"]] = float(tariffs.get(int(c_from["id"]), {}).get(int(c_to["id"]), 0.0))
        tariff_rows.append(row)
    st.dataframe(pd.DataFrame(tariff_rows), width="stretch", hide_index=True)

    st.markdown("### Historial de políticas pasadas")
    policies = get_past_policy_table(int(world["id"]), max_period=visible_history_limit(world, user["role"]))
    if policies:
        st.dataframe(pd.DataFrame(policies), width="stretch", hide_index=True)
    else:
        st.info("Todavía no hay historial de políticas.")

    if world["status"] != "finished":
        st.markdown("### Estado de envíos del período")
        st.dataframe(
            pd.DataFrame(period_submission_status(int(world["id"]), int(world["current_period"]))),
            width="stretch",
            hide_index=True,
        )

    st.markdown("### Gráficos")
    render_graph_tabs(world, default_country_name=my_country_name, key_prefix=f"user_{country_id}")

    if world["status"] == "finished":
        st.success("La partida terminó.")
        st.markdown("### Ranking final")
        st.dataframe(ranking_df(world), width="stretch", hide_index=True)
        return

    if user["role"] != "country_leader":
        st.info("Esta cuenta es solo de visualización. Solo la cuenta líder puede enviar políticas.")
        return

    if my_state["required_gov_delta_next"] is not None:
        st.warning(
            f"Tarjeta roja activa: en este período debes enviar una variación de gasto <= {my_state['required_gov_delta_next']}%."
        )

    existing_submissions = get_submissions_for_period(int(world["id"]), int(world["current_period"]))
    existing = existing_submissions.get(country_id, {})
    limits = world["settings"]["limits"]

    st.markdown("### Enviar políticas para el período")
    with st.form(f"country_policy_form_{country_id}"):
        col1, col2 = st.columns(2)
        with col1:
            fx_delta = st.number_input(
                "Variación del tipo de cambio (%)",
                min_value=-float(limits["fx_delta_abs"]),
                max_value=float(limits["fx_delta_abs"]),
                value=float(existing.get("fx_delta", 0.0)),
                step=0.5,
            )
            gov_delta = st.number_input(
                "Variación del gasto estatal (%)",
                min_value=-float(limits["gov_delta_abs"]),
                max_value=float(limits["gov_delta_abs"]),
                value=float(existing.get("gov_delta", 0.0)),
                step=0.5,
            )
        with col2:
            vat_delta = st.number_input(
                "Cambio del IVA (pp)",
                min_value=-float(limits["vat_delta_abs"]),
                max_value=float(limits["vat_delta_abs"]),
                value=float(existing.get("vat_delta", 0.0)),
                step=0.5,
            )
            public_emp_delta = st.number_input(
                "Variación del empleo público (%)",
                min_value=-float(limits["public_emp_delta_abs"]),
                max_value=float(limits["public_emp_delta_abs"]),
                value=float(existing.get("public_emp_delta", 0.0)),
                step=1.0,
            )

        st.markdown("#### Cambios de aranceles por socio")
        tariff_changes_existing = existing.get("tariff_changes", {})
        tariff_changes: Dict[int, float] = {}
        for c in countries:
            partner_id = int(c["id"])
            if partner_id == country_id:
                continue
            current_tariff = float(tariffs.get(country_id, {}).get(partner_id, 0.0))
            tariff_changes[partner_id] = st.number_input(
                f"Δ arancel contra {c['name']} (actual: {current_tariff:.1f}%)",
                min_value=-float(limits["tariff_delta_abs"]),
                max_value=float(limits["tariff_delta_abs"]),
                value=float(tariff_changes_existing.get(partner_id, 0.0)),
                step=0.5,
                key=f"country_tariff_{country_id}_{partner_id}",
            )

        submitted = st.form_submit_button("Enviar políticas", width="stretch")
        if submitted:
            invalid = False
            if my_state["required_gov_delta_next"] is not None and float(gov_delta) > float(my_state["required_gov_delta_next"]):
                st.error(
                    f"Debes enviar una variación de gasto <= {my_state['required_gov_delta_next']}% por la tarjeta roja vigente."
                )
                invalid = True

            final_vat = float(my_state["vat_rate"]) + float(vat_delta)
            if final_vat < 0 or final_vat > 100:
                st.error("El IVA final debe quedar entre 0% y 100%.")
                invalid = True

            for partner_id, delta in tariff_changes.items():
                current_tariff = float(tariffs.get(country_id, {}).get(int(partner_id), 0.0))
                final_tariff = current_tariff + float(delta)
                if final_tariff < 0 or final_tariff > 100:
                    st.error("Un arancel debe quedar entre 0% y 100%.")
                    invalid = True
                    break

            if not invalid:
                upsert_submission(
                    world_id=int(world["id"]),
                    period_no=int(world["current_period"]),
                    country_id=country_id,
                    submitted_by_user_id=int(user["id"]),
                    fx_delta=float(fx_delta),
                    gov_delta=float(gov_delta),
                    vat_delta=float(vat_delta),
                    public_emp_delta=float(public_emp_delta),
                    tariff_changes=tariff_changes,
                )
                st.success("Políticas enviadas. Puedes reenviar y sobrescribir mientras el período siga abierto.")
                st.rerun()

    if existing:
        if st.button("Eliminar mi envío actual", width="stretch"):
            clear_submission(int(world["id"]), int(world["current_period"]), country_id)
            st.success("Envío eliminado.")
            st.rerun()


# -----------------------------
# Shell general
# -----------------------------
def sidebar_shell() -> Dict[str, Any] | None:
    user = st.session_state.auth_user
    with st.sidebar:
        st.title("Demo SIER-like")
        if user:
            st.write(f"**Usuario:** {user['username']}")
            st.write(f"**Rol:** {user['role']}")
        if st.button("Cerrar sesión", width="stretch"):
            st.session_state.auth_user = None
            st.rerun()

        st.markdown("---")
        st.caption("MVP multiusuario con cronómetro, países, líder y profesor")
    return user


# -----------------------------
# Main
# -----------------------------
def main() -> None:
    user = st.session_state.auth_user
    if user is None:
        login_screen()
        return

    sidebar_shell()
    world = get_active_world()

    if user["role"] == "admin":
        st.title("🧑‍🏫 Panel del profesor")
        if world is not None:
            admin_current_world_panel(world)
        world_creation_panel()
        return

    if world is None:
        st.info("Todavía no hay un mundo activo. Espera a que el profesor cree la partida.")
        return

    country_dashboard(user, world)


if __name__ == "__main__":
    main()
