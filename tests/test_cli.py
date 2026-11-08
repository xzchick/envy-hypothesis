"""CLI tests that need no display. The passphrase prompt is patched."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from envy_hypothesis import cli


class CLITest(unittest.TestCase):
    SECRET = b"a long private analysis passphrase"

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="envy-hypothesis-cli-"))
        self.addCleanup(__import__("shutil").rmtree, self.dir)
        self._n = 0

    def seq(self):
        self._n += 1
        return self.dir / f"a.r{self._n}.envyhypo"

    def invoke(self, *args, secret=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch.object(cli, "_passphrase", return_value=secret or self.SECRET):
            code = cli.main([str(a) for a in args])
        return code, out.getvalue(), err.getvalue()

    def _id(self, out, needle):
        return out.split(needle)[1].split(" ")[0]

    def test_version(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as ctx:
            cli.main(["--version"])
        self.assertEqual(ctx.exception.code, 0)
        self.assertIn("envy-hypothesis", out.getvalue())

    def test_full_ach_flow(self):
        base = self.dir / "a.envyhypo"
        code, _, _ = self.invoke("init", base, "--title", "Outage", "--question", "Why down?")
        self.assertEqual(code, 0)
        p1 = self.seq()
        _, out, _ = self.invoke("hypothesis", base, p1, "--label", "Sabotage")
        h_bad = self._id(out, "hypothesis added ")
        p2 = self.seq()
        _, out, _ = self.invoke("hypothesis", p1, p2, "--label", "Routine failure")
        h_ok = self._id(out, "hypothesis added ")
        p3 = self.seq()
        _, out, _ = self.invoke("evidence", p2, p3, "--text", "No intrusion found", "--credibility", "3")
        e1 = self._id(out, "evidence added ")
        # Rate: evidence very-inconsistent with Sabotage, consistent with Routine.
        p4 = self.seq()
        code, _, _ = self.invoke("rate", p3, p4, "--evidence", e1, "--hypothesis", h_bad,
                                 "--value", "very-inconsistent")
        self.assertEqual(code, 0)
        p5 = self.seq()
        self.invoke("rate", p4, p5, "--evidence", e1, "--hypothesis", h_ok, "--value", "consistent")
        code, out, _ = self.invoke("score", p5)
        self.assertEqual(code, 0)
        self.assertIn("Least contradicted: Routine failure", out)

    def test_matrix_and_inspect(self):
        base = self.dir / "a.envyhypo"
        self.invoke("init", base, "--title", "T")
        p1 = self.seq()
        _, out, _ = self.invoke("hypothesis", base, p1, "--label", "H1")
        hid = self._id(out, "hypothesis added ")
        p2 = self.seq()
        _, out, _ = self.invoke("evidence", p1, p2, "--text", "E1")
        eid = self._id(out, "evidence added ")
        p3 = self.seq()
        self.invoke("rate", p2, p3, "--evidence", eid, "--hypothesis", hid, "--value", "2")
        code, out, _ = self.invoke("matrix", p3)
        self.assertEqual(code, 0)
        grid = json.loads(out)
        self.assertEqual(grid["evidence"][0]["ratings"][hid]["value"], 2)
        code, out, _ = self.invoke("inspect", p3)
        data = json.loads(out)
        self.assertEqual(data["journal"][-1]["kind"], "rated")

    def test_export_writes_report(self):
        base = self.dir / "a.envyhypo"
        self.invoke("init", base, "--title", "T")
        p1 = self.seq()
        self.invoke("hypothesis", base, p1, "--label", "Only one")
        out_html = self.dir / "r.html"
        code, out, _ = self.invoke("export", p1, out_html, "--format", "html")
        self.assertEqual(code, 0)
        self.assertIn("sha256", out)
        self.assertTrue(out_html.exists())

    def test_bad_passphrase_is_error(self):
        base = self.dir / "a.envyhypo"
        self.invoke("init", base, "--title", "T")
        code, _, err = self.invoke("score", base, secret=b"the wrong passphrase!")
        self.assertEqual(code, 2)
        self.assertIn("passphrase", err.lower())

    def test_unknown_command(self):
        with self.assertRaises(SystemExit):
            cli.build_parser().parse_args(["seance"])


if __name__ == "__main__":
    unittest.main()
