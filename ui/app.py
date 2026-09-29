"""Interface web para demonstrar a API de recomendação.

Tudo passa pela API (POST /recommend, POST /feedback, GET /arms): a interface não
acessa o banco nem o Redis. O endereço da API vem de API_URL.

Uso local: streamlit run ui/app.py   (com a API em http://localhost:8000)
"""

import os
from datetime import date

import altair as alt
import pandas as pd
import requests
import streamlit as st

API_URL = os.getenv("API_URL", "http://localhost:8000").rstrip("/")
TIMEOUT = 15

MONTHS = {
    "jan": "Janeiro", "feb": "Fevereiro", "mar": "Março", "apr": "Abril", "may": "Maio", "jun": "Junho",
    "jul": "Julho", "aug": "Agosto", "sep": "Setembro", "oct": "Outubro", "nov": "Novembro", "dec": "Dezembro",
}  # fmt: skip
ARMS = {
    "celular_inicio_semana": "Celular, seg a qua",
    "celular_fim_semana": "Celular, qui e sex",
    "telefone_inicio_semana": "Telefone fixo, seg a qua",
    "telefone_fim_semana": "Telefone fixo, qui e sex",
}
SEGMENTS = {"jovem": "Jovem (até 29)", "adulto": "Adulto (30 a 59)", "senior": "Sênior (60+)"}
GOLDEN = {
    900001: "900001 · 24 anos, estudante",
    900002: "900002 · 41 anos, administrativo",
    900003: "900003 · 67 anos, aposentado, já converteu antes",
    900004: "900004 · 35 anos, operário",
    900005: "900005 · 29 anos, técnico",
}
CHOSEN, OTHER = "#2a78d6", "#c3c2b7"


def call(method: str, path: str, **kwargs) -> tuple[int, dict]:
    try:
        response = requests.request(method, f"{API_URL}{path}", timeout=TIMEOUT, **kwargs)
    except requests.RequestException as exc:
        return 0, {"detail": f"API fora do ar em {API_URL}: {exc.__class__.__name__}"}
    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, {"detail": response.text}


def show_error(status: int, body: dict) -> None:
    st.error(f"Erro {status or ''}: {body.get('detail', body)}")


def scores_chart(scores: dict, chosen: str) -> alt.Chart:
    data = pd.DataFrame(
        {
            "braço": [ARMS[a] for a in scores],
            "sorteio": list(scores.values()),
            "escolhido": ["sim" if a == chosen else "não" for a in scores],
        }
    )
    base = alt.Chart(data).encode(
        y=alt.Y("braço:N", sort="-x", title=None, axis=alt.Axis(labelLimit=220, labelFontSize=12)),
        x=alt.X("sorteio:Q", title="taxa sorteada da Beta", axis=alt.Axis(format="%")),
    )
    bars = base.mark_bar(cornerRadiusEnd=4, height=22).encode(
        color=alt.Color("escolhido:N", scale=alt.Scale(domain=["sim", "não"], range=[CHOSEN, OTHER]), legend=None),
        tooltip=[alt.Tooltip("braço:N"), alt.Tooltip("sorteio:Q", format=".2%")],
    )
    labels = base.mark_text(align="left", dx=4, fontSize=12).encode(text=alt.Text("sorteio:Q", format=".1%"))
    return (bars + labels).properties(height=170)


st.set_page_config(page_title="Recomendador de ofertas", page_icon="📞", layout="wide")
st.session_state.setdefault("history", [])

with st.sidebar:
    st.header("Recomendador de ofertas")
    st.caption("Multi-Armed Bandit com Thompson Sampling")
    status, _ = call("GET", "/health")
    if status == 200:
        st.success("API no ar")
    else:
        st.error("API indisponível")
    st.caption(f"API: {API_URL}")
    st.markdown(f"[Swagger da API]({API_URL}/docs)")
    st.divider()
    st.markdown(
        "**Como funciona:** para cada cliente, o modelo sorteia uma taxa de conversão para cada forma de "
        "contato e escolhe a maior. Cada resposta (converteu ou não) atualiza o modelo na hora."
    )

tab_rec, tab_state, tab_history = st.tabs(["Recomendar", "O que o modelo aprendeu", "Histórico da sessão"])

# ---------------------------------------------------------------------------
# Recomendar
# ---------------------------------------------------------------------------
with tab_rec:
    col_client, col_month, col_button = st.columns([3, 2, 1], vertical_alignment="bottom")
    with col_client:
        options = [*GOLDEN, "outro"]
        picked = st.selectbox(
            "Cliente",
            options,
            format_func=lambda c: GOLDEN.get(c, "Outro id da base..."),
        )
        client_id = picked
        if picked == "outro":
            client_id = st.number_input("Id do cliente", min_value=1, value=1, step=1)
    with col_month:
        month_codes = list(MONTHS)
        month = st.selectbox(
            "Mês da campanha",
            month_codes,
            index=date.today().month - 1,
            format_func=MONTHS.get,
        )
    with col_button:
        if st.button("Recomendar", type="primary", width="stretch"):
            status, body = call("POST", "/recommend", json={"client_id": int(client_id), "campaign_month": month})
            if status == 200:
                st.session_state["rec"] = body
                st.session_state["feedback"] = None
                st.session_state["history"].insert(
                    0,
                    {
                        "id": body["recommendation_id"],
                        "cliente": body["client_id"],
                        "contexto": body["context"],
                        "oferta": ARMS[body["recommended_offer"]],
                        "resultado": "aguardando",
                    },
                )
            else:
                st.session_state["rec"] = None
                show_error(status, body)

    rec = st.session_state.get("rec")
    if rec:
        st.divider()
        left, right = st.columns([2, 3])
        with left:
            st.caption("Oferta recomendada")
            st.subheader(ARMS[rec["recommended_offer"]])
            st.markdown(
                f"**Segmento:** {SEGMENTS.get(rec['segment'], rec['segment'])}  \n"
                f"**Mês da campanha:** {MONTHS[rec['campaign_month']]}  \n"
                f"**Contexto no modelo:** `{rec['context']}`  \n"
                f"**Leitura das features no Redis:** {rec['feature_latency_ms']:.1f} ms  \n"
                f"**Recomendação nº:** {rec['recommendation_id']}"
            )
        with right:
            st.caption("Sorteio de cada forma de contato (a maior vence)")
            st.altair_chart(scores_chart(rec["sampled_scores"], rec["recommended_offer"]), width="stretch")

        with st.expander("Dados do cliente vindos da Feature Store (Redis)"):
            st.dataframe(pd.DataFrame([rec["features"]]), hide_index=True, width="stretch")

        st.markdown("**O cliente converteu?** A resposta atualiza o modelo.")
        feedback = st.session_state.get("feedback")
        b1, b2, _ = st.columns([1, 1, 3])
        for column, reward, label in ((b1, 1, "Sim, converteu"), (b2, 0, "Não converteu")):
            if column.button(label, disabled=feedback is not None, width="stretch", key=f"fb{reward}"):
                status, body = call(
                    "POST", "/feedback", json={"recommendation_id": rec["recommendation_id"], "reward": reward}
                )
                if status == 200:
                    st.session_state["feedback"] = body
                    for item in st.session_state["history"]:
                        if item["id"] == rec["recommendation_id"]:
                            item["resultado"] = "converteu" if reward else "não converteu"
                    st.rerun()
                else:
                    show_error(status, body)
        if feedback:
            total = feedback["successes"] + feedback["failures"]
            st.success(
                f"Modelo atualizado: em **{feedback['context']}**, a oferta *{ARMS[feedback['offer']]}* "
                f"agora tem {feedback['successes']} conversões em {total} contatos."
            )

# ---------------------------------------------------------------------------
# Estado do modelo
# ---------------------------------------------------------------------------
with tab_state:
    state_month = st.selectbox(
        "Mês", list(MONTHS), index=date.today().month - 1, format_func=MONTHS.get, key="state_month"
    )
    status, arms = call("GET", "/arms", params={"month": state_month})
    if status != 200:
        show_error(status, arms)
    else:
        rows = [
            {
                "segmento": SEGMENTS[context.split("|")[0]],
                "oferta": ARMS[arm],
                "conversão esperada": stats["expected_conversion"],
                "contatos": stats["successes"] + stats["failures"],
                "conversões": stats["successes"],
            }
            for context, by_arm in arms.items()
            for arm, stats in by_arm.items()
        ]
        table = pd.DataFrame(rows)
        if table["contatos"].sum() == 0:
            st.info(
                f"Ainda não há dados de {MONTHS[state_month]}: "
                "o modelo parte do zero nesse mês e aprende com o feedback."
            )
        pivot = table.pivot(index="segmento", columns="oferta", values="conversão esperada")
        pivot = pivot.reindex(index=list(SEGMENTS.values()), columns=list(ARMS.values()))
        st.caption(
            f"Conversão esperada por segmento e oferta em {MONTHS[state_month]} (a melhor de cada linha está destacada)"
        )
        st.dataframe(
            pivot.style.format("{:.1%}").highlight_max(axis=1, color="#cde2fb"),
            width="stretch",
        )
        with st.expander("Contadores (sucessos e contatos)"):
            st.dataframe(table.style.format({"conversão esperada": "{:.1%}"}), hide_index=True, width="stretch")

# ---------------------------------------------------------------------------
# Histórico
# ---------------------------------------------------------------------------
with tab_history:
    if st.session_state["history"]:
        st.dataframe(pd.DataFrame(st.session_state["history"]), hide_index=True, width="stretch")
    else:
        st.info("As recomendações feitas nesta sessão aparecem aqui.")
