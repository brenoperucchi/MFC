"""
FERRAMENTA DE PREPARAÇÃO DE DADOS, NÃO PARTE DO PIPELINE DE PRODUÇÃO.

Baixa OHLC M1 gratuito da HistData.com (via o pacote `histdata` do PyPI) e
agrega em barras MN1 (mensais), só para os pares/anos que a instância MT5
isolada (`mfc-backtest`, conta Exness-MT5Trial11 198819543) não tem — 27 dos
28 pares travam em 59 ou 152 barras MN1 comuns, mas o gate OOS exige 169
(`count=60` + 109 de aquecimento do ATR(100)). GBPJPY já tem 400+ barras na
Exness e está fora desta lista.

Este script produz SOMENTE dado de aquecimento (a parte antiga da série,
usada para o ATR convergir) — nunca dado de decisão. As barras mais recentes
usadas pela matriz continuam vindo ao vivo da Exness. Ver
`scripts/backtest_canonical.py::load_mn1_series_with_warmup` pro adaptador
que consome este cache, e `docs/plans/port-upstream-institutional-matrix.md`
pro registro da decisão e da validação cruzada contra a Exness.

Validado em 2026-08-31: comparação HistData vs. Exness no período de
sobreposição (set-dez/2021, os únicos meses em que os dois conjuntos
coincidem para os pares de 59 barras) deu diferença média de 0,17% e máxima
de 0,81% no close mensal, sem viés sistemático — consistente com ruído
normal de spread entre corretoras, não erro de mapeamento de símbolo/fuso.

Uso:
    pip install histdata   # numa venv, não no ambiente do projeto
    python scripts/fetch_histdata_mn1_warmup.py [par ...]

Sem argumentos, baixa/completa todos os pares em PAIRS. Idempotente: se o
par já tem cache, só os anos de YEARS que ainda faltam nele são baixados
(nunca reescreve um ano já presente) — rodar de novo depois de estender
YEARS (como aconteceu 2026-09-01, 2012-2021 -> 2010-2021) completa o cache
existente em vez de pular o par inteiro. O cache bruto (zips por par/ano)
fica em data/histdata_mn1_warmup/.raw_cache/ pra evitar rebaixar em uma
nova tentativa.
"""

import csv
import io
import json
import os
import sys
import time
import zipfile
from datetime import datetime

# `histdata` só é importado dentro de fetch_pair() (lazy), não aqui no topo
# do módulo: scripts/backtest_canonical.py importa find_gaps() deste
# arquivo (função pura, sem I/O) pra reaproveitar a checagem de
# contiguidade sem precisar do pacote `histdata` instalado no ambiente
# normal do projeto — só quem efetivamente baixa dado precisa dele.

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT_DIR = os.path.join(BASE_DIR, "data", "histdata_mn1_warmup")
RAW_CACHE_DIR = os.path.join(OUT_DIR, ".raw_cache")

# gbpjpy tem 400+ barras MN1 na Exness (sem déficit) -- não faz parte desta
# lista. Os demais 27 têm déficit de 17 (eurusd/gbpusd, que já tinham 152) a
# 110 (os outros 25, que travam em 59) barras contra o requisito de 169.
PAIRS = [
    "eurusd", "gbpusd", "audusd", "nzdusd", "usdcad", "usdchf", "usdjpy",
    "eurgbp", "euraud", "eurcad", "eurchf", "eurjpy", "eurnzd",
    "gbpaud", "gbpcad", "gbpchf", "gbpnzd",
    "audcad", "audchf", "audjpy", "audnzd",
    "cadchf", "cadjpy",
    "chfjpy",
    "nzdcad", "nzdchf", "nzdjpy",
]
# 2010-2021 = 144 meses. Era 2012-2021 (120 meses) até a janela OOS
# estendida [2023-01-01,...) (pedido do usuário, 2026-09-01) revelar um
# déficit de ~4 meses no required_full_history_bars pros 18 pares "cross"
# (nem canônico USD, nem AUDJPY/CHFJPY, que por motivo histórico distinto
# já tinham 2010-2021 desde o início) — 2010-2021 fecha com folga e
# uniformiza a baseline entre os 27 pares.
YEARS = list(range(2010, 2022))


def log(msg):
    print(f"[{datetime.now().isoformat(timespec='seconds')}] {msg}", flush=True)


def find_gaps(months):
    """Meses ausentes DENTRO do intervalo [min(months), max(months)] de um
    dict {"YYYY-MM": ...} — não avalia o que existe antes/depois do
    intervalo coberto, só buracos internos. Retorna a lista de chaves
    "YYYY-MM" ausentes, em ordem cronológica; [] se contíguo.

    Compartilhada entre este fetcher e
    scripts/backtest_canonical.py::load_mn1_series_with_warmup — achado
    herdr-review mfc-61 (P2-1): antes não havia checagem de contiguidade em
    lugar nenhum do pipeline, então um cache com buraco no meio passava
    como se fosse completo."""
    if not months:
        return []
    keys = sorted(months)
    year, month = (int(part) for part in keys[0].split("-"))
    end_year, end_month = (int(part) for part in keys[-1].split("-"))
    gaps = []
    while (year, month) <= (end_year, end_month):
        key = f"{year:04d}-{month:02d}"
        if key not in months:
            gaps.append(key)
        month += 1
        if month > 12:
            month = 1
            year += 1
    return gaps


def parse_zip_to_monthly(zip_path):
    """Lê o CSV M1 dentro do zip e agrega em OHLC mensal. Retorna
    {"YYYY-MM": {"open":, "high":, "low":, "close":, "n": <barras M1 no mês>}}."""
    months = {}
    with zipfile.ZipFile(zip_path) as zf:
        csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not csv_names:
            return months
        with zf.open(csv_names[0]) as raw:
            text = io.TextIOWrapper(raw, encoding="ascii", errors="ignore")
            for row in csv.reader(text, delimiter=";"):
                if len(row) < 5:
                    continue
                try:
                    ts = datetime.strptime(row[0], "%Y%m%d %H%M%S")
                    o, h, l, c = (float(row[1]), float(row[2]), float(row[3]), float(row[4]))
                except ValueError:
                    continue
                key = f"{ts.year:04d}-{ts.month:02d}"
                bucket = months.get(key)
                if bucket is None:
                    months[key] = {"open": o, "high": h, "low": l, "close": c, "n": 1}
                else:
                    bucket["high"] = max(bucket["high"], h)
                    bucket["low"] = min(bucket["low"], l)
                    bucket["close"] = c
                    bucket["n"] += 1
    return months


def year_is_complete(months, year, current_year):
    """Um ANO (não um mês) está completo em `months` (dict {"YYYY-MM": ...})
    quando os 12 meses existem — exceto o ano corrente, que nunca vai ter
    os 12 se ainda está em curso (usa "algum mês presente" pra esse caso).

    Achado herdr-review mfc-64 (MFC64-02/`mfc-rev` + P3-3/`mfc-rev-2`,
    CONFIRMADO pelos dois): a versão anterior de fetch_pair() considerava um
    ano "presente" com QUALQUER mês nele — exatamente o formato do buraco
    real do AUDJPY (o zip de 2012 só tinha outubro). Rodar o fetcher de
    novo nunca completaria um ano assim; extraída como função pura pra dar
    pra testar sem precisar mockar download_hist_data."""
    if year == current_year:
        return any(k.startswith(f"{year:04d}-") for k in months)
    return all(f"{year:04d}-{m:02d}" in months for m in range(1, 13))


def fetch_pair(pair):
    try:
        from histdata import download_hist_data
    except ImportError:
        print(
            "[-] Pacote `histdata` não instalado. Rode numa venv isolada:\n"
            "    python -m venv /tmp/histdata_venv && "
            "/tmp/histdata_venv/bin/pip install histdata\n"
            "    /tmp/histdata_venv/bin/python scripts/fetch_histdata_mn1_warmup.py"
        )
        sys.exit(1)
    out_path = os.path.join(OUT_DIR, f"{pair}.json")
    all_months = {}
    if os.path.exists(out_path):
        with open(out_path, "r", encoding="utf-8") as f:
            all_months = json.load(f)
        # Achado do probe de 2026-09-01 (usuário + exec): a versão anterior
        # pulava o par inteiro se o arquivo já existisse, mesmo quando YEARS
        # crescia depois (2012-2021 -> 2010-2021) — reproduzir a extensão
        # exigia um script separado, não versionado (resíduo que
        # `mfc-rev-2` já tinha apontado pro merge do Dukascopy/AUDJPY).
        # Agora completa só os anos que faltam, sem tocar nos que já tem —
        # ver year_is_complete() (achado herdr-review mfc-64, MFC64-02/P3-3).
        current_year = datetime.now().year
        missing_years = [
            y for y in YEARS if not year_is_complete(all_months, y, current_year)
        ]
        if not missing_years:
            log(f"{pair}: já cobre {YEARS[0]}-{YEARS[-1]}, pulando")
            return
        log(f"{pair}: já baixado parcialmente, completando ano(s) {missing_years}")
    else:
        missing_years = list(YEARS)
    os.makedirs(RAW_CACHE_DIR, exist_ok=True)
    for year in missing_years:
        zip_path = os.path.join(RAW_CACHE_DIR, f"DAT_ASCII_{pair.upper()}_M1_{year}.zip")
        if not os.path.exists(zip_path):
            for attempt in range(3):
                try:
                    download_hist_data(
                        year=str(year), pair=pair, output_directory=RAW_CACHE_DIR,
                        verbose=False,
                    )
                    break
                except Exception as exc:  # noqa: BLE001
                    log(f"{pair} {year}: tentativa {attempt + 1} falhou: {exc}")
                    time.sleep(3)
            else:
                log(f"{pair} {year}: desistindo após 3 tentativas")
                continue
        if not os.path.exists(zip_path):
            log(f"{pair} {year}: sem dado disponível (provavelmente anterior ao início deste par)")
            continue
        months = parse_zip_to_monthly(zip_path)
        all_months.update(months)
        time.sleep(1.5)  # ser educado com o servidor da histdata.com
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_months, f, indent=2, sort_keys=True)
    log(f"{pair}: {len(all_months)} meses -> {out_path}")
    gaps = find_gaps(all_months)
    if gaps:
        # Achado herdr-review mfc-61 (P2-1, mfc-rev e mfc-rev-2, confirmado
        # pelos dois): a primeira versão deste script escrevia o cache
        # mesmo com buracos no meio, sem avisar — a "correção" de um caso
        # real (AUDJPY) só adicionou mais meses na PONTA, sem fechar o
        # buraco, e ninguém percebeu porque nada aqui checava contiguidade.
        # Um buraco no meio do intervalo agora é IMPRESSO alto, mesmo que
        # o cache ainda seja gravado (a ausência pode ser real do lado da
        # HistData.com, não um erro de fetch — ver AUDJPY 2012 no plano:
        # o zip re-baixado só tinha outubro, confirmando que a fonte
        # gratuita genuinamente não tem os outros 11 meses desse ano pra
        # esse par).
        log(
            f"[!] {pair}: {len(gaps)} mes(es) ausente(s) DENTRO do intervalo "
            f"{min(all_months)}..{max(all_months)}: {', '.join(gaps)}"
        )


def main():
    pairs = [p.lower() for p in sys.argv[1:]] or PAIRS
    log(f"Baixando {len(pairs)} par(es), anos {YEARS[0]}-{YEARS[-1]}")
    for pair in pairs:
        fetch_pair(pair)
    log("Concluído")


if __name__ == "__main__":
    main()
