import unittest
from unittest.mock import patch

from agents.triad_analyzer import analyze_tf_triad, REGION_ZONA_PARADA, REGION_EQUILIBRIO, REGION_EXTREMO


class TestRegionThresholdFrozen(unittest.TestCase):
    """Item 3 do plano de reconciliação com o upstream (Miquéias, 27/08): ele
    mudou o threshold de região/box 3 vezes em 3 dias (+/-0.20 original ->
    banda +/-0.01 -> banda alargada +/-0.04), sinal de tuning ainda ativo do
    lado dele. Decisão: MANTER nosso valor em +/-0.20 (já era o valor real em
    uso — mesmo default de docs/MATHEMATICAL_MODELS.md, mt5/css.mql5
    `inp_levelCrossValue` e CSS.pine `boxLevel`). Estes testes CONGELAM esse
    valor com casos de fronteira exatos: se algum dia REGION_ZONA_PARADA for
    alterado sem querer (ou de propósito, sem decisão explícita), a suíte
    acusa."""

    def test_frozen_constants_match_documented_values(self):
        self.assertEqual(REGION_ZONA_PARADA, 0.20)
        self.assertEqual(REGION_EQUILIBRIO, 0.05)
        self.assertEqual(REGION_EXTREMO, 0.50)

    def test_boundary_exactly_at_zona_parada_verde_is_inclusive(self):
        r = analyze_tf_triad("D1", [0.0, 0.20])
        self.assertEqual(r["region_type"], "ZONA_PARADA_VERDE")

    def test_boundary_just_below_zona_parada_verde_is_box_superior(self):
        r = analyze_tf_triad("D1", [0.0, 0.199])
        self.assertEqual(r["region_type"], "BOX_SUPERIOR")

    def test_boundary_exactly_at_zona_parada_vermelha_is_inclusive(self):
        r = analyze_tf_triad("D1", [0.0, -0.20])
        self.assertEqual(r["region_type"], "ZONA_PARADA_VERMELHA")

    def test_boundary_just_above_zona_parada_vermelha_is_box_inferior(self):
        r = analyze_tf_triad("D1", [0.0, -0.199])
        self.assertEqual(r["region_type"], "BOX_INFERIOR")

    def test_boundary_equilibrio_inclusive_both_sides(self):
        self.assertEqual(analyze_tf_triad("D1", [0.0, 0.05])["region_type"], "EQUILIBRIO_0")
        self.assertEqual(analyze_tf_triad("D1", [0.0, -0.05])["region_type"], "EQUILIBRIO_0")

    def test_boundary_just_outside_equilibrio_is_box(self):
        self.assertEqual(analyze_tf_triad("D1", [0.0, 0.051])["region_type"], "BOX_SUPERIOR")
        self.assertEqual(analyze_tf_triad("D1", [0.0, -0.051])["region_type"], "BOX_INFERIOR")

    def test_boundary_extremo_superior_inclusive(self):
        r = analyze_tf_triad("D1", [0.0, 0.50])
        self.assertEqual(r["region_type"], "EXTREMO_SUPERIOR")

    def test_boundary_just_below_extremo_is_zona_parada_verde(self):
        r = analyze_tf_triad("D1", [0.0, 0.499])
        self.assertEqual(r["region_type"], "ZONA_PARADA_VERDE")

    def test_boundary_extremo_inferior_inclusive(self):
        r = analyze_tf_triad("D1", [0.0, -0.50])
        self.assertEqual(r["region_type"], "EXTREMO_INFERIOR")

    def test_boundary_just_above_negative_extremo_is_zona_parada_vermelha(self):
        r = analyze_tf_triad("D1", [0.0, -0.499])
        self.assertEqual(r["region_type"], "ZONA_PARADA_VERMELHA")

    def test_last_extreme_tracking_uses_the_same_frozen_boundary(self):
        """O rastreamento de origem do ciclo (GREEN/RED) usa o mesmo
        +/-0.20 — testado à parte porque é um segundo consumidor da
        constante, não só a classificação de região em si."""
        r = analyze_tf_triad("D1", [0.20, 0.10, 0.05, 0.0, -0.10])
        self.assertEqual(r["region_type"], "BOX_INFERIOR")
        self.assertEqual(r["owing_cycle"], "Devendo Ciclo de Baixa rumo à Linha Vermelha (-0.20)")

    def test_cycle_text_interpolates_the_constant_not_hardcoded(self):
        """Achado em revisão (mfc-rev-2, herdr-review rodada 20, P3-1): os
        textos de current_cycle/owing_cycle tinham "+0.20"/"-0.20" escritos
        à mão — recalibrar REGION_ZONA_PARADA fazia "region" citar o valor
        novo enquanto esses dois campos continuavam citando 0.20, uma saída
        autocontraditória que vai pro dashboard e pro relatório diário. Este
        teste recalibra de propósito e prova que os três campos concordam."""
        import agents.triad_analyzer as triad
        with patch.object(triad, "REGION_ZONA_PARADA", 0.16):
            r = triad.analyze_tf_triad("D1", [0.10, 0.17])
        self.assertEqual(r["region_type"], "ZONA_PARADA_VERDE")
        self.assertIn("+0.16", r["region"])
        self.assertIn("+0.16", r["current_cycle"])
        self.assertNotIn("0.20", r["current_cycle"])
        self.assertNotIn("0.20", r["owing_cycle"])


if __name__ == "__main__":
    unittest.main()
