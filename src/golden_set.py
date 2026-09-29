"""Golden Set: 5 clientes fictícios usados como casos de teste de ponta a ponta.

Eles são gravados na Offline Store junto com os clientes reais (IDs a partir de
900001, fora da faixa do dataset) e, após a materialização, também ficam
disponíveis no Redis para testar a API.

GOLDEN_MONTHS define o mês da campanha de cada caso de teste: a recomendação depende
do contexto (segmento do cliente x mês), então cada caso fixa um mês diferente.
"""

GOLDEN_CLIENTS = [
    {
        "client_id": 900001,
        "age": 24,
        "job": "student",
        "education": "university.degree",
        "housing": "no",
        "loan": "no",
        "previous": 0,
        "days_since_last_contact": -1,
        "poutcome": "nonexistent",
    },
    {
        "client_id": 900002,
        "age": 41,
        "job": "admin.",
        "education": "high.school",
        "housing": "yes",
        "loan": "no",
        "previous": 0,
        "days_since_last_contact": -1,
        "poutcome": "nonexistent",
    },
    {
        "client_id": 900003,
        "age": 67,
        "job": "retired",
        "education": "basic.4y",
        "housing": "no",
        "loan": "no",
        "previous": 1,
        "days_since_last_contact": 6,
        "poutcome": "success",
    },
    {
        "client_id": 900004,
        "age": 35,
        "job": "blue-collar",
        "education": "basic.9y",
        "housing": "yes",
        "loan": "yes",
        "previous": 0,
        "days_since_last_contact": -1,
        "poutcome": "nonexistent",
    },
    {
        "client_id": 900005,
        "age": 29,
        "job": "technician",
        "education": "professional.course",
        "housing": "yes",
        "loan": "no",
        "previous": 2,
        "days_since_last_contact": -1,
        "poutcome": "failure",
    },
]

GOLDEN_MONTHS = {900001: "may", 900002: "aug", 900003: "may", 900004: "nov", 900005: "sep"}
