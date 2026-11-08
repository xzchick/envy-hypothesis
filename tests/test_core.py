"""Core Hypothesis tests: the encrypted envelope, the ACH model (hypotheses,
evidence, ratings), the journal hash chain, revision-preserving saves, the ACH
scoring (least-contradicted ranking + evidence diagnosticity), export, and the
no-overwrite guarantee.
"""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from envy_hypothesis import core
from envy_hypothesis.core import HypothesisError


class HypothesisTest(unittest.TestCase):
    SECRET = b"a long private analysis passphrase"

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="envy-hypothesis-"))
        self.addCleanup(__import__("shutil").rmtree, self.dir)
        self._n = 0

    def analysis(self, name="a.envyhypo", title="Who did it?", question="What explains the outage?"):
        path = self.dir / name
        core.create(path, title, self.SECRET, question)
        return path

    def _seq(self):
        self._n += 1
        return self.dir / f"a.r{self._n}.envyhypo"

    # ----- envelope -----------------------------------------------------
    def test_encrypted_round_trip_and_magic(self):
        blob = core.encrypt_bytes(b"analysis", self.SECRET)
        self.assertTrue(blob.startswith(core.MAGIC))
        self.assertEqual(blob[:11], b"ENVYVAULT2\x00")  # byte-compatible with ENVY Vault
        self.assertEqual(core.decrypt_bytes(blob, self.SECRET), b"analysis")

    def test_short_passphrase_rejected(self):
        with self.assertRaises(HypothesisError):
            core.create(self.dir / "short.envyhypo", "T", b"tooshort")

    def test_wrong_passphrase_is_a_clean_error(self):
        path = self.analysis()
        with self.assertRaises(HypothesisError):
            core._load(path, b"the wrong passphrase!!")

    # ----- create / mutate ---------------------------------------------
    def test_create_lays_out_analysis(self):
        path = self.analysis(title="Night shift", question="Why down?")
        data, _ = core._load(path, self.SECRET)
        self.assertEqual(data["revision"], 0)
        self.assertEqual(data["title"], "Night shift")
        self.assertEqual(data["question"], "Why down?")
        self.assertEqual((data["hypotheses"], data["evidence"], data["journal"]), ([], [], []))

    def _two_by_two(self):
        """Two hypotheses, two evidence items, with ratings that favour H_benign."""
        p = self.analysis()
        p1 = self._seq(); h_bad = core.add_hypothesis(p, p1, self.SECRET, "Sabotage")["hypothesis_id"]
        p2 = self._seq(); h_ok = core.add_hypothesis(p1, p2, self.SECRET, "Routine failure")["hypothesis_id"]
        p3 = self._seq(); e1 = core.add_evidence(p2, p3, self.SECRET, "No signs of intrusion",
                                                 "log review", credibility=3)["evidence_id"]
        p4 = self._seq(); e2 = core.add_evidence(p3, p4, self.SECRET, "Disk was already failing",
                                                 "SMART report", credibility=2)["evidence_id"]
        # E1 "no intrusion": very inconsistent with Sabotage, consistent with Routine.
        p5 = self._seq(); core.rate(p4, p5, self.SECRET, e1, h_bad, -2)
        p6 = self._seq(); core.rate(p5, p6, self.SECRET, e1, h_ok, 1)
        # E2 "disk failing": neutral to Sabotage, very consistent with Routine.
        p7 = self._seq(); core.rate(p6, p7, self.SECRET, e2, h_bad, 0)
        p8 = self._seq(); core.rate(p7, p8, self.SECRET, e2, h_ok, 2)
        return p8, {"bad": h_bad, "ok": h_ok, "e1": e1, "e2": e2}

    def test_add_sorts_and_preserves_revisions(self):
        p = self.analysis()
        p1 = self._seq(); core.add_hypothesis(p, p1, self.SECRET, "First")
        data, _ = core._load(p1, self.SECRET)
        self.assertEqual(data["revision"], 1)
        self.assertEqual(len(data["hypotheses"]), 1)
        self.assertTrue(p.exists() and p1.exists())  # the earlier revision is kept

    # ----- ACH scoring --------------------------------------------------
    def test_score_ranks_least_contradicted_first(self):
        path, ids = self._two_by_two()
        rep = core.score(path, self.SECRET)
        self.assertEqual(rep["leading"]["label"], "Routine failure")
        top = rep["hypotheses"][0]
        self.assertEqual(top["label"], "Routine failure")
        self.assertEqual(top["rank"], 1)
        # Sabotage carries weighted inconsistency 3*2 (E1 very-inconsistent, cred 3) = 6.
        sabotage = next(h for h in rep["hypotheses"] if h["label"] == "Sabotage")
        self.assertEqual(sabotage["weighted_inconsistency"], 6)
        self.assertEqual(top["weighted_inconsistency"], 0)
        self.assertEqual(rep["separation"], 6)
        self.assertTrue(rep["decisive"])

    def test_diagnosticity_flags_discriminating_evidence(self):
        path, ids = self._two_by_two()
        rep = core.score(path, self.SECRET)
        diag = {d["id"]: d for d in rep["evidence_diagnosticity"]}
        # E1 spans -2..1 across hypotheses (spread 3) × cred 3 = 9: diagnostic.
        self.assertTrue(diag[ids["e1"]]["diagnostic"])
        self.assertEqual(diag[ids["e1"]]["diagnosticity"], 9)
        # Most-diagnostic sorts first.
        self.assertEqual(rep["evidence_diagnosticity"][0]["id"], ids["e1"])

    def test_non_diagnostic_evidence_is_flagged(self):
        p = self.analysis()
        p1 = self._seq(); h1 = core.add_hypothesis(p, p1, self.SECRET, "A")["hypothesis_id"]
        p2 = self._seq(); h2 = core.add_hypothesis(p1, p2, self.SECRET, "B")["hypothesis_id"]
        p3 = self._seq(); e = core.add_evidence(p2, p3, self.SECRET, "Consistent with everything",
                                                credibility=4)["evidence_id"]
        p4 = self._seq(); core.rate(p3, p4, self.SECRET, e, h1, 1)
        p5 = self._seq(); core.rate(p4, p5, self.SECRET, e, h2, 1)  # same rating both ways
        rep = core.score(p5, self.SECRET)
        d = rep["evidence_diagnosticity"][0]
        self.assertFalse(d["diagnostic"])
        self.assertEqual(d["spread"], 0)

    def test_rating_aliases_and_clear(self):
        p = self.analysis()
        p1 = self._seq(); h = core.add_hypothesis(p, p1, self.SECRET, "A")["hypothesis_id"]
        p2 = self._seq(); e = core.add_evidence(p1, p2, self.SECRET, "E", credibility=1)["evidence_id"]
        p3 = self._seq(); core.rate(p2, p3, self.SECRET, e, h, "very-inconsistent")
        data, _ = core._load(p3, self.SECRET)
        self.assertEqual(data["evidence"][0]["ratings"][h], -2)
        p4 = self._seq(); core.rate(p3, p4, self.SECRET, e, h, "NA")  # clear it
        data, _ = core._load(p4, self.SECRET)
        self.assertEqual(data["evidence"][0]["ratings"], {})

    def test_remove_hypothesis_strips_its_ratings(self):
        path, ids = self._two_by_two()
        p = self._seq()
        core.remove_hypothesis(path, p, self.SECRET, ids["bad"])
        data, _ = core._load(p, self.SECRET)
        self.assertEqual(len(data["hypotheses"]), 1)
        for item in data["evidence"]:
            self.assertNotIn(ids["bad"], item["ratings"])  # the column is gone everywhere

    def test_rate_rejects_unknown_ids(self):
        path, ids = self._two_by_two()
        with self.assertRaises(HypothesisError):
            core.rate(path, self._seq(), self.SECRET, ids["e1"], "11111111-1111-1111-1111-111111111111", 1)
        with self.assertRaises(HypothesisError):
            core.rate(path, self._seq(), self.SECRET, "22222222-2222-2222-2222-222222222222", ids["bad"], 1)

    # ----- journal / tamper --------------------------------------------
    def test_journal_is_hash_chained(self):
        path, _ = self._two_by_two()
        data, _ = core._load(path, self.SECRET)
        self.assertEqual(data["journal"][0]["previous_sha256"], "0" * 64)
        self.assertEqual(data["journal"][1]["previous_sha256"], data["journal"][0]["record_sha256"])

    def test_tampered_journal_record_breaks_chain(self):
        # The journal is hash-chained: editing a recorded event (even with the right
        # passphrase) no longer matches its stored digest, so the chain is rejected.
        path, _ = self._two_by_two()
        data, _ = core._load(path, self.SECRET)
        data["journal"][0]["detail"] = "forged detail"
        forged = self.dir / "forged.envyhypo"
        core.new_file(forged, core.encrypt_bytes(core._canonical(data), self.SECRET))
        with self.assertRaises(HypothesisError):
            core.inspect(forged, self.SECRET)

    # ----- export -------------------------------------------------------
    def test_export_html_and_json(self):
        path, _ = self._two_by_two()
        html_out = self.dir / "r.html"
        res = core.export(path, html_out, self.SECRET, "html")
        text = html_out.read_text()
        self.assertEqual(res["format"], "html")
        self.assertIn("Content-Security-Policy", text)
        self.assertIn("Routine failure", text)
        self.assertIn("least contradicted", text.lower())
        json_out = self.dir / "r.json"
        core.export(path, json_out, self.SECRET, "json")
        report = json.loads(json_out.read_text())
        self.assertEqual(report["leading"]["label"], "Routine failure")
        self.assertIn("limitations", report)

    def test_export_escapes_markup(self):
        p = self.analysis()
        p1 = self._seq(); core.add_hypothesis(p, p1, self.SECRET, "<script>alert(1)</script>")
        out = self.dir / "x.html"
        core.export(p1, out, self.SECRET, "html")
        body = out.read_text()
        self.assertNotIn("<script>alert(1)</script>", body)
        self.assertIn("&lt;script&gt;", body)

    # ----- no overwrite -------------------------------------------------
    def test_new_file_never_overwrites(self):
        keep = self.dir / "keep"
        keep.write_bytes(b"precious")
        with self.assertRaises(HypothesisError):
            core.new_file(keep, b"clobber")
        self.assertEqual(keep.read_bytes(), b"precious")

    def test_export_refuses_to_overwrite(self):
        path = self.analysis()
        dest = self.dir / "exists.html"; dest.write_bytes(b"keep me")
        with self.assertRaises(HypothesisError):
            core.export(path, dest, self.SECRET, "html")
        self.assertEqual(dest.read_bytes(), b"keep me")


if __name__ == "__main__":
    unittest.main()
