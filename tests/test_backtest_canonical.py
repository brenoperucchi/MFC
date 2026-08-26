"""
Testa scripts/backtest_canonical.py — que nunca teve suíte própria (achado
em revisão: Codex + mfc-rev-2, achado 4 rodada 3, confirmado pelos dois
independentemente).

O laço principal (run()) depende de MT5, séries em disco e o motor de
confluência inteiro — não vale a pena mocká-los só pra testar a lógica de
aviso. Por isso as duas peças que importam foram extraídas pra funções
puras, testáveis sem nenhum desses mocks:

  - _tally_cost_quality: LÊ o CostModel e incrementa os contadores — é onde
    o bug da rodada 3 morava de verdade (o laço não lia
    last_basket_swap_unmodeled).
  - _cost_quality_summary_lines: FORMATA os contadores em mensagens.

Achado em revisão (Codex + mfc-rev-2, achado 4 rodada 4, confirmado pelos
dois): a rodada 3 só testou a segunda função. O Claude reproduziu apagando
a leitura de last_basket_swap_unmodeled do laço original — a suíte
continuava toda verde, porque nenhum teste tocava a LIGAÇÃO. Isto testa as
duas separadamente.
"""
import io
import unittest
from contextlib import redirect_stdout
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

import scripts.backtest_canonical as bc


class TestTallyCostQuality(unittest.TestCase):
    """A REGRESSÃO CENTRAL do achado 4 rodada 4: onde o bug da rodada 3
    realmente morava — não na formatação da mensagem, na LEITURA do
    CostModel. Um fake com os dois atributos, sem precisar de MT5/séries/
    confluência nenhuma."""

    @staticmethod
    def _fake_costs(degraded, swap_unmodeled):
        return SimpleNamespace(last_basket_degraded=degraded,
                               last_basket_swap_unmodeled=swap_unmodeled)

    def test_increments_degraded_when_present(self):
        d, s = bc._tally_cost_quality(self._fake_costs({"CADJPY"}, set()), 0, 0)
        self.assertEqual((d, s), (1, 0))
        print("[✓] Cesta com dado perdido incrementa degraded_baskets")

    def test_increments_swap_unmodeled_even_when_degraded_is_empty(self):
        """Este é EXATAMENTE o caso que a rodada 3 deixava mudo: degraded
        vazio, swap_unmodeled preenchido. Se alguém remover a leitura de
        last_basket_swap_unmodeled (o bug real da rodada 3), este teste
        falha — ao contrário dos testes de formatação, que continuavam
        verdes com esse bug presente."""
        d, s = bc._tally_cost_quality(self._fake_costs(set(), {"CADJPY"}), 0, 0)
        self.assertEqual((d, s), (0, 1),
                         "swap_unmodeled não incrementou — é o bug exato da rodada 3")
        print("[✓] Cesta com swap não modelado (degraded vazio) incrementa "
              "swap_unmodeled_baskets — o bug da rodada 3 não volta")

    def test_does_not_increment_on_clean_basket(self):
        d, s = bc._tally_cost_quality(self._fake_costs(set(), set()), 0, 0)
        self.assertEqual((d, s), (0, 0))
        print("[✓] Cesta limpa não incrementa nenhum contador")

    def test_accumulates_across_multiple_calls(self):
        """Simula o laço real: várias cestas, contadores acumulando."""
        d, s = 0, 0
        for degraded, unmodeled in [(set(), set()), ({"CADJPY"}, set()),
                                    (set(), {"EURCAD"}), ({"USDCAD"}, {"GBPCAD"})]:
            d, s = bc._tally_cost_quality(self._fake_costs(degraded, unmodeled), d, s)
        self.assertEqual((d, s), (2, 2))
        print("[✓] Contadores acumulam corretamente ao longo de várias cestas")


class TestCostQualitySummaryLines(unittest.TestCase):
    """A REGRESSÃO CENTRAL do achado 4 rodada 3: a rodada anterior ligou
    last_basket_swap_unmodeled no logger ao vivo (measure_and_log_basket_cost)
    mas esqueceu o backtest — que nunca leu essa bandeira. Numa corretora com
    swap fora de PONTOS, last_basket_degraded ficava vazio, o único aviso
    que existia nunca disparava, e ~45% do custo real desaparecia do PnL
    LÍQUIDO em silêncio (medido por mfc-rev-2)."""

    def test_no_warning_when_nothing_degraded_or_unmodeled(self):
        lines = bc._cost_quality_summary_lines(baskets=10, degraded_baskets=0,
                                                swap_unmodeled_baskets=0)
        self.assertEqual(lines, [])
        print("[✓] Caminho feliz não gera aviso nenhum")

    def test_warns_about_degraded_data(self):
        lines = bc._cost_quality_summary_lines(baskets=10, degraded_baskets=3,
                                                swap_unmodeled_baskets=0)
        self.assertEqual(len(lines), 1)
        self.assertIn("3/10", lines[0])
        self.assertIn("OTIMISTA", lines[0])
        print("[✓] Dado perdido gera o aviso de PnL otimista")

    def test_warns_about_swap_unmodeled_even_with_zero_degraded(self):
        """Este é o caso exato que a rodada anterior deixava mudo: nenhuma
        perna com dado perdido (degraded=0), mas todas as cestas com swap
        fora de pontos. Sem esta correção, nenhuma linha seria devolvida —
        e o operador nunca saberia que ~45% do custo de swap ficou de fora."""
        lines = bc._cost_quality_summary_lines(baskets=10, degraded_baskets=0,
                                                swap_unmodeled_baskets=10)
        self.assertEqual(len(lines), 1,
                         "swap_unmodeled sozinho tem que gerar aviso, mesmo com degraded=0")
        self.assertIn("10/10", lines[0])
        self.assertNotIn("OTIMISTA", lines[0],
                         "swap não modelado não é a mesma classe de problema que dado "
                         "perdido — não pode usar o mesmo tom de alarme")
        print("[✓] swap_unmodeled sozinho (degraded=0) ainda gera aviso — o bug da rodada "
              "anterior não volta")

    def test_both_categories_produce_two_separate_lines(self):
        """As duas categorias não podem se fundir numa mensagem só — cada
        uma tem uma causa e uma gravidade diferente."""
        lines = bc._cost_quality_summary_lines(baskets=10, degraded_baskets=2,
                                                swap_unmodeled_baskets=5)
        self.assertEqual(len(lines), 2)
        degraded_line = next(l for l in lines if "OTIMISTA" in l)
        swap_line = next(l for l in lines if "OTIMISTA" not in l)
        self.assertIn("2/10", degraded_line)
        self.assertIn("5/10", swap_line)
        print("[✓] Dado perdido e swap não modelado geram duas linhas separadas, não uma só")


class TestRunWiresCostQualityIntoSummary(unittest.TestCase):
    """Achado em revisão (Codex P3 + mfc-rev-2 P2-3, rodada 5, confirmado
    pelos dois independentemente): as classes acima cobrem
    _tally_cost_quality e _cost_quality_summary_lines ISOLADAS, mas nada
    provava que run() de fato chama as duas — a rodada 3 já teve esse
    bug exato (apagar as duas linhas do laço deixava a suíte inteira
    verde) e a rodada 4 provou que o mesmo bug ainda é reintroduzível
    apagando qualquer uma das DUAS chamadas (a de dentro do laço ou a do
    resumo final), não só as duas linhas originais. Fecha a fronteira com
    um smoke test de run() — uma noite, uma cesta — injetando as seis
    dependências externas (ensure_mt5, load_series, load_h1_prices,
    evaluate_at, convert_pnl_to_usd, CostModel), nenhuma delas MT5 real."""

    class _FakeCostModel:
        """Substitui CostModel inteiro: .basket() devolve um custo fixo e
        marca a cesta como tendo uma perna degradada — o suficiente pra
        provar que run() LÊ last_basket_degraded via _tally_cost_quality
        e IMPRIME o aviso via _cost_quality_summary_lines, não só que a
        chamada não quebra."""

        def __init__(self, lot):
            self.lot = lot
            self.last_basket_degraded = set()
            self.last_basket_swap_unmodeled = set()

        def basket(self, ccy, bias, leg_lots=None):
            self.last_basket_degraded = {"USDCAD"}
            self.last_basket_swap_unmodeled = set()
            return 2.5

    def _run_one_night_one_basket(self):
        now = pd.Timestamp.now()
        h1_times = pd.Series(pd.date_range(end=now, periods=48, freq="h"))
        fake_series = {tf: {"times": h1_times, "scores": {}} for tf in bc.TFS}

        class _FakePriceSeries:
            def asof(self, _dt):
                return 1.1000

        fake_prices = {pair: _FakePriceSeries() for pair in bc.ALL_28_PAIRS}

        def fake_evaluate_at(series, entry_server_dt):
            return {"USD": {"trade_bias": "COMPRA"}}

        buf = io.StringIO()
        with patch.object(bc, "ensure_mt5", return_value=True), \
             patch.object(bc, "load_series", return_value=fake_series), \
             patch.object(bc, "load_h1_prices", return_value=fake_prices), \
             patch.object(bc, "evaluate_at", side_effect=fake_evaluate_at), \
             patch.object(bc, "convert_pnl_to_usd", return_value=(10.0, 5.0)), \
             patch.object(bc, "CostModel", self._FakeCostModel), \
             redirect_stdout(buf):
            bc.run(days=1)
        return buf.getvalue()

    def test_run_prints_degraded_warning_from_real_cost_model_state(self):
        output = self._run_one_night_one_basket()
        self.assertIn("cesta(s) tiveram ao menos uma perna sem símbolo/", output,
                       "run() não imprimiu o aviso de custo degradado — a ligação entre "
                       "o laço, _tally_cost_quality e _cost_quality_summary_lines quebrou")
        self.assertIn("OTIMISTA", output)
        print("[✓] run() lê last_basket_degraded do CostModel de cada cesta e imprime "
              "o aviso no resumo final — a fronteira que só a extração não cobria")

    def test_run_prints_swap_unmodeled_note_from_real_cost_model_state(self):
        """Achado em revisão (Codex, rodada 6, F-07): o smoke test acima só
        exercitava last_basket_degraded — o fake sempre fixava
        last_basket_swap_unmodeled=set(), e a asserção só procurava o aviso
        de degradação. Apagar a leitura/contagem da bandeira de SWAP no
        laço de run() (o bug histórico original do achado 4, rodada 2→3)
        ainda deixava esse smoke verde. Fecha com a variante inversa:
        degraded vazio, swap_unmodeled preenchido."""

        class _SwapUnmodeledFakeCostModel(self._FakeCostModel):
            def basket(self, ccy, bias, leg_lots=None):
                self.last_basket_degraded = set()
                self.last_basket_swap_unmodeled = {"USDCAD"}
                return 2.5

        now = pd.Timestamp.now()
        h1_times = pd.Series(pd.date_range(end=now, periods=48, freq="h"))
        fake_series = {tf: {"times": h1_times, "scores": {}} for tf in bc.TFS}

        class _FakePriceSeries:
            def asof(self, _dt):
                return 1.1000

        fake_prices = {pair: _FakePriceSeries() for pair in bc.ALL_28_PAIRS}

        buf = io.StringIO()
        with patch.object(bc, "ensure_mt5", return_value=True), \
             patch.object(bc, "load_series", return_value=fake_series), \
             patch.object(bc, "load_h1_prices", return_value=fake_prices), \
             patch.object(bc, "evaluate_at", return_value={"USD": {"trade_bias": "COMPRA"}}), \
             patch.object(bc, "convert_pnl_to_usd", return_value=(10.0, 5.0)), \
             patch.object(bc, "CostModel", _SwapUnmodeledFakeCostModel), \
             redirect_stdout(buf):
            bc.run(days=1)
        output = buf.getvalue()
        self.assertNotIn("cesta(s) tiveram ao menos uma perna sem símbolo/", output,
                         "swap_unmodeled sozinho não pode disparar o aviso de dado perdido")
        self.assertIn("swap_mode fora de PONTOS", output,
                       "run() não imprimiu a nota de swap não modelado — a ligação entre "
                       "o laço e last_basket_swap_unmodeled quebrou")
        print("[✓] run() lê last_basket_swap_unmodeled do CostModel e imprime a nota "
              "correspondente, independente da bandeira de degradação")

    def test_run_does_not_warn_when_no_basket_is_degraded(self):
        """Controle negativo: sem degradação, o aviso não pode aparecer —
        prova que o teste acima não passa por outro motivo qualquer."""

        class _CleanFakeCostModel(self._FakeCostModel):
            def basket(self, ccy, bias, leg_lots=None):
                self.last_basket_degraded = set()
                self.last_basket_swap_unmodeled = set()
                return 2.5

        now = pd.Timestamp.now()
        h1_times = pd.Series(pd.date_range(end=now, periods=48, freq="h"))
        fake_series = {tf: {"times": h1_times, "scores": {}} for tf in bc.TFS}

        class _FakePriceSeries:
            def asof(self, _dt):
                return 1.1000

        fake_prices = {pair: _FakePriceSeries() for pair in bc.ALL_28_PAIRS}

        buf = io.StringIO()
        with patch.object(bc, "ensure_mt5", return_value=True), \
             patch.object(bc, "load_series", return_value=fake_series), \
             patch.object(bc, "load_h1_prices", return_value=fake_prices), \
             patch.object(bc, "evaluate_at", return_value={"USD": {"trade_bias": "COMPRA"}}), \
             patch.object(bc, "convert_pnl_to_usd", return_value=(10.0, 5.0)), \
             patch.object(bc, "CostModel", _CleanFakeCostModel), \
             redirect_stdout(buf):
            bc.run(days=1)
        output = buf.getvalue()
        self.assertNotIn("cesta(s) tiveram ao menos uma perna sem símbolo/", output)
        print("[✓] sem cesta degradada, o aviso não aparece — controle negativo do teste acima")


if __name__ == "__main__":
    unittest.main()
