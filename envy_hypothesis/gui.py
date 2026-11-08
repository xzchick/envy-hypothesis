"""A native ENVY Hypothesis window: a real desktop app, not a web view.

Built with PyQt6 and styled in the ENVY palette with a live animated header.
Lay out competing hypotheses and evidence, rate how consistent each piece of
evidence is with each hypothesis, and read which hypothesis is least contradicted
and which evidence actually discriminates. Every save is a new revision; earlier
revisions are never overwritten.

Nothing is sent: no network, no accounts.
"""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path

from . import __version__
from . import core
from .core import HypothesisError

BG = "#050713"
PANEL = "#0a0e22"
PANEL_HI = "#121735"
INK = "#f2f4ff"
MUTED = "#b9c3df"
DIM = "#6f7898"
PINK = "#ff3db4"
CYAN = "#35e8ff"
LAVENDER = "#ac8cff"
VIOLET = "#8547e8"
AMBER = "#ffb347"
GREEN = "#49e3a6"
LINE = "#1f2547"

# Colour and short glyph for each consistency rating.
RATING_COLOR = {2: GREEN, 1: CYAN, 0: DIM, -1: AMBER, -2: PINK}
RATING_GLYPH = {2: "++", 1: "+", 0: "0", -1: "−", -2: "−−"}


def lerp(a, b, t):
    from PyQt6.QtGui import QColor
    ca, cb = QColor(a), QColor(b)
    return QColor(int(ca.red() + (cb.red() - ca.red()) * t),
                  int(ca.green() + (cb.green() - ca.green()) * t),
                  int(ca.blue() + (cb.blue() - ca.blue()) * t))


def _build():
    from PyQt6.QtCore import Qt, QTimer, QRectF, QPointF
    from PyQt6.QtGui import (QColor, QPainter, QLinearGradient, QRadialGradient, QFont,
                             QPen, QBrush)
    from PyQt6.QtWidgets import (QApplication, QWidget, QLabel, QPushButton, QPlainTextEdit,
                                 QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView,
                                 QAbstractItemView, QVBoxLayout, QHBoxLayout, QInputDialog,
                                 QFileDialog, QMessageBox, QLineEdit as QLE)

    class Header(QWidget):
        def __init__(self):
            super().__init__()
            self.setFixedHeight(172)
            self.phase = 0.0
            self.stars = [(((i * 139 + 37) % 1000) / 1000.0,
                           ((i * 83 + 19) % 172) / 172.0,
                           0.6 + (i % 3) * 0.5, (i % 7) / 7.0) for i in range(60)]

        def tick(self, phase):
            self.phase = phase
            self.update()

        def paintEvent(self, _event):
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            w, h = self.width(), self.height()
            p.fillRect(self.rect(), QColor(BG))
            for cx, cy, rad, col, a in ((w - 120, 60, 250, LAVENDER, 46),
                                        (60, h, 280, VIOLET, 52), (w * 0.5, -40, 240, CYAN, 40)):
                g = QRadialGradient(cx, cy, rad)
                c = QColor(col); c.setAlpha(a); g.setColorAt(0, c)
                c2 = QColor(col); c2.setAlpha(0); g.setColorAt(1, c2)
                p.setBrush(QBrush(g)); p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(QPointF(cx, cy), rad, rad)
            for sx, sy, sr, ph in self.stars:
                tw = 0.5 + 0.5 * math.sin(self.phase / 0.5 + ph * 6.28)
                c = QColor(205, 217, 255); c.setAlphaF(0.15 + 0.6 * tw)
                p.setBrush(c); p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(QPointF(sx * w, sy * h), sr, sr)
            self._fork_mark(p, 48, 54, 22)
            pulse = 0.5 + 0.5 * math.sin(self.phase)
            p.setFont(QFont("Helvetica", 26, QFont.Weight.Bold))
            grad = QLinearGradient(88, 0, 540, 0)
            grad.setColorAt(0, QColor(CYAN))
            grad.setColorAt(0.5, QColor(lerp(CYAN, "#ffffff", 0.2 * pulse)))
            grad.setColorAt(1, QColor(PINK))
            glow = QColor(VIOLET); glow.setAlpha(int(55 + 55 * pulse))
            p.setPen(QPen(glow, 1))
            for dx, dy in ((1.3, 1.3), (-1.3, 1.3), (1.3, -1.3)):
                p.drawText(QRectF(88 + dx, 38 + dy, 640, 48), Qt.AlignmentFlag.AlignVCenter, "ENVY HYPOTHESIS")
            p.setPen(QPen(QBrush(grad), 1))
            p.drawText(QRectF(88, 38, 640, 48), Qt.AlignmentFlag.AlignVCenter, "ENVY HYPOTHESIS")
            p.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
            p.setPen(QColor(LAVENDER))
            p.drawText(QRectF(90, 92, 760, 20), Qt.AlignmentFlag.AlignVCenter,
                       "ENCRYPTED  ·  COMPETING HYPOTHESES  ·  WEIGHED")
            y = 126
            p.setPen(QPen(QColor(LINE), 2)); p.drawLine(90, y, w - 54, y)
            p.setPen(QPen(QColor(VIOLET), 3)); p.drawLine(90, y, 162, y)
            span = (w - 54) - 90
            x = 90 + span * ((self.phase * 0.12) % 1.0)
            halo = QRadialGradient(x, y, 15)
            hc = QColor(CYAN); hc.setAlpha(170); halo.setColorAt(0, hc)
            hc2 = QColor(CYAN); hc2.setAlpha(0); halo.setColorAt(1, hc2)
            p.setBrush(QBrush(halo)); p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(x, y), 15, 15)
            p.setBrush(QColor("#ffffff")); p.drawEllipse(QPointF(x, y), 2.5, 2.5)
            p.end()

        def _fork_mark(self, p, cx, cy, r):
            """A single premise forking into competing hypotheses; one branch leads."""
            pulse = 0.5 + 0.5 * math.sin(self.phase)
            aura = QRadialGradient(cx, cy, r + 9)
            c = QColor(LAVENDER); c.setAlpha(int(70 + 60 * pulse)); aura.setColorAt(0, c)
            c2 = QColor(LAVENDER); c2.setAlpha(0); aura.setColorAt(1, c2)
            p.setBrush(QBrush(aura)); p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(cx, cy), r + 9, r + 9)
            # The premise node, lower-left; three branches fan out to the right.
            bx, by = cx - r * 0.7, cy + r * 0.6
            p.setBrush(QColor("#ffffff")); p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(bx, by), 3.0, 3.0)
            lead = int((self.phase * 0.25) % 3)          # which branch is "leading" drifts slowly
            for k, ang in enumerate((-0.95, -0.4, 0.15)):
                ex, ey = bx + (r * 1.5) * math.cos(ang), by + (r * 1.5) * math.sin(ang)
                grad = QLinearGradient(bx, by, ex, ey)
                grad.setColorAt(0, QColor(VIOLET))
                grad.setColorAt(1, QColor(GREEN if k == lead else PINK if k == 2 else CYAN))
                width = 3.2 if k == lead else 1.8
                p.setPen(QPen(QBrush(grad), width)); p.drawLine(QPointF(bx, by), QPointF(ex, ey))
                node = QColor(GREEN if k == lead else PINK if k == 2 else CYAN)
                if k != lead:
                    node.setAlphaF(0.5 + 0.4 * pulse)
                p.setBrush(node); p.setPen(Qt.PenStyle.NoPen)
                rad = 3.6 if k == lead else 2.4
                p.drawEllipse(QPointF(ex, ey), rad, rad)

    def make_table(headers):
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setDefaultSectionSize(28)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setAlternatingRowColors(True)
        table.horizontalHeader().setStretchLastSection(True)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        return table

    ID_ROLE = int(Qt.ItemDataRole.UserRole)

    class Window(QWidget):
        def __init__(self):
            super().__init__()
            self.analysis = None
            self.secret = None
            self.data = None
            self.report = None
            self.hyp_ids = []          # column order in the matrix, left to right
            self.setWindowTitle("ENVY Hypothesis")
            self.resize(1200, 880)
            self.setMinimumSize(1040, 760)
            self.setStyleSheet(STYLE)
            root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)
            self.header = Header(); root.addWidget(self.header)

            body = QWidget(); body.setObjectName("body")
            bl = QVBoxLayout(body); bl.setContentsMargins(22, 14, 22, 16); bl.setSpacing(10)

            intro = QLabel("List competing hypotheses and the evidence, rate how consistent each piece of "
                           "evidence is with each hypothesis, and ACH ranks the hypotheses by how little "
                           "the evidence contradicts them. Every save is a new revision. Nothing is sent.")
            intro.setObjectName("status"); intro.setWordWrap(True); bl.addWidget(intro)

            controls = QHBoxLayout()
            for caption, handler, name in (("New", self.new_analysis, "go"),
                                           ("Open", self.open_analysis, "pick"),
                                           ("Lock", self.lock, "pick"),
                                           ("Add hypothesis", self.add_hypothesis, "go"),
                                           ("Add evidence", self.add_evidence, "go"),
                                           ("Rate cell", self.rate_cell, "go"),
                                           ("Reweight", self.reweight_evidence, "pick"),
                                           ("Copy id", self.copy_id, "pick"),
                                           ("Remove", self.remove_current, "pick"),
                                           ("Export", self.export_report, "pick")):
                b = QPushButton(caption); b.setObjectName(name)
                b.setCursor(Qt.CursorShape.PointingHandCursor); b.clicked.connect(handler)
                controls.addWidget(b)
            controls.addStretch(1)
            bl.addLayout(controls)

            self.mon_header = QPlainTextEdit(); self.mon_header.setObjectName("mono")
            self.mon_header.setReadOnly(True); self.mon_header.setFixedHeight(84)
            self.mon_header.setPlainText("No analysis open. Create a new analysis or open an existing one.")
            bl.addWidget(self.mon_header)

            self.views = QTabWidget(); self.views.setObjectName("tabs")
            self.t_matrix = make_table(["Evidence", "Cred"])
            self.t_matrix.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
            self.t_rank = make_table(["Rank", "Hypothesis", "Against (weighted)", "For (weighted)", "Rated"])
            self.t_history = make_table(["Logged UTC", "Event", "Detail"])
            self.views.addTab(self.t_matrix, "Matrix")
            self.views.addTab(self.t_rank, "Ranking")
            self.views.addTab(self.t_history, "History")
            bl.addWidget(self.views, 1)

            self.path_label = QLabel(""); self.path_label.setObjectName("foot")
            self.path_label.setWordWrap(True); bl.addWidget(self.path_label)
            foot = QLabel("ACH organises your reasoning; it does not supply facts. The ratings and weights are "
                          "judgements you enter, the ranking is arithmetic on them, and a small separation "
                          "between the top hypotheses means the evidence is not decisive.")
            foot.setObjectName("foot"); foot.setWordWrap(True); bl.addWidget(foot)
            root.addWidget(body, 1)

            self.phase = 0.0
            self.timer = QTimer(self); self.timer.timeout.connect(self._animate); self.timer.start(33)
            self._set_enabled(False)

        # ----- lifecycle ----------------------------------------------------
        def _ask_passphrase(self, confirm):
            text, ok = QInputDialog.getText(self, "Passphrase",
                                            "Analysis passphrase (at least 12 characters):", QLE.EchoMode.Password)
            if not ok:
                return None
            secret = text.encode("utf-8")
            if confirm:
                again, ok2 = QInputDialog.getText(self, "Confirm passphrase",
                                                  "Repeat the passphrase:", QLE.EchoMode.Password)
                if not ok2:
                    return None
                if again.encode("utf-8") != secret:
                    self._warn("Passphrases do not match."); return None
            try:
                return core.check_passphrase(secret)
            except HypothesisError as exc:
                self._warn(str(exc)); return None

        def new_analysis(self):
            title, ok = QInputDialog.getText(self, "New analysis", "Analysis title:")
            if not ok or not title.strip():
                return
            question, ok = QInputDialog.getText(self, "Key question",
                                                "The question you are analysing (optional):")
            if not ok:
                return
            dest, _ = QFileDialog.getSaveFileName(self, "Save the encrypted analysis", "analysis.envyhypo",
                                                  "Analysis (*.envyhypo);;All files (*)")
            if not dest:
                return
            secret = self._ask_passphrase(confirm=True)
            if secret is None:
                return
            try:
                if Path(dest).exists():
                    Path(dest).unlink()
                core.create(dest, title.strip(), secret, question.strip())
                self._open_path(Path(dest), secret)
                self._warn("Analysis created. Add two or more competing hypotheses, then the evidence.",
                           icon=QMessageBox.Icon.Information, title="Analysis created")
            except (HypothesisError, OSError) as exc:
                self._warn(str(exc))

        def open_analysis(self):
            path, _ = QFileDialog.getOpenFileName(self, "Open an encrypted analysis", "",
                                                  "Analysis (*.envyhypo);;All files (*)")
            if not path:
                return
            secret = self._ask_passphrase(confirm=False)
            if secret is None:
                return
            self._open_path(Path(path), secret)

        def _open_path(self, path, secret):
            try:
                data, _ = core._load(path, secret)
                report = core.score(path, secret)
            except HypothesisError as exc:
                self._warn(str(exc)); return
            self.analysis, self.secret, self.data, self.report = path, secret, data, report
            self._set_enabled(True)
            self._refresh_all()

        def lock(self):
            self.analysis = self.secret = self.data = self.report = None
            self.hyp_ids = []
            for t in (self.t_matrix, self.t_rank, self.t_history):
                t.setRowCount(0)
            self.t_matrix.setColumnCount(2)
            self.t_matrix.setHorizontalHeaderLabels(["Evidence", "Cred"])
            self.mon_header.setPlainText("Locked. The passphrase was cleared from memory.")
            self.path_label.setText("")
            self._set_enabled(False)

        def _reopen(self, path):
            self._open_path(Path(path), self.secret)

        # ----- mutations ----------------------------------------------------
        def add_hypothesis(self):
            if not self.data:
                return
            label, ok = QInputDialog.getText(self, "Add hypothesis",
                                             "State a competing hypothesis (an explanation to test):")
            if not ok or not label.strip():
                return
            self._mutate(lambda dest: core.add_hypothesis(self.analysis, dest, self.secret, label.strip()), tab=0)

        def add_evidence(self):
            if not self.data:
                return
            text, ok = QInputDialog.getText(self, "Add evidence", "The evidence or argument:")
            if not ok or not text.strip():
                return
            source, ok = QInputDialog.getText(self, "Source", "Where it came from (optional):")
            if not ok:
                return
            cred, ok = QInputDialog.getInt(self, "Credibility",
                                           "How much weight does it carry? (1 weak – 5 strong)", 3,
                                           core.MIN_CREDIBILITY, core.MAX_CREDIBILITY)
            if not ok:
                return
            self._mutate(lambda dest: core.add_evidence(self.analysis, dest, self.secret,
                                                        text.strip(), source.strip(), cred), tab=0)

        def rate_cell(self):
            if not self.data:
                return
            ev_id, hyp_id, hyp_label, ev_text = self._selected_cell()
            if not ev_id or not hyp_id:
                self._warn("In the Matrix tab, click the cell where an evidence row meets a hypothesis "
                           "column, then choose how consistent they are."); return
            options = ["very consistent (++)", "consistent (+)", "neutral (0)",
                       "inconsistent (−)", "very inconsistent (−−)", "clear this cell"]
            values = [2, 1, 0, -1, -2, None]
            choice, ok = QInputDialog.getItem(
                self, "Rate cell",
                f"How consistent is this evidence with the hypothesis?\n\nEvidence: {ev_text[:80]}\n"
                f"Hypothesis: {hyp_label[:80]}", options, 1, False)
            if not ok:
                return
            value = values[options.index(choice)]
            self._mutate(lambda dest: core.rate(self.analysis, dest, self.secret, ev_id, hyp_id, value), tab=0)

        def reweight_evidence(self):
            if not self.data:
                return
            ev_id, ev_text = self._selected_evidence()
            if not ev_id:
                self._warn("Select an evidence row in the Matrix tab first."); return
            cred, ok = QInputDialog.getInt(self, "Reweight evidence",
                                           f"Credibility for “{ev_text[:60]}” (1 weak – 5 strong):", 3,
                                           core.MIN_CREDIBILITY, core.MAX_CREDIBILITY)
            if not ok:
                return
            self._mutate(lambda dest: core.reweight(self.analysis, dest, self.secret, ev_id, cred), tab=0)

        def copy_id(self):
            if not self.data:
                return
            if self.views.currentIndex() == 1:
                hyp_id, label = self._selected_hypothesis()
                target, what = hyp_id, label
            else:
                target, what = self._selected_evidence()
            if not target:
                self._warn("Select a row (an evidence item in Matrix, or a hypothesis in Ranking) first."); return
            QApplication.clipboard().setText(target)
            self._warn(f"Copied the id for “{what}” to the clipboard:\n{target}",
                       icon=QMessageBox.Icon.Information, title="Id copied")

        def remove_current(self):
            if not self.data:
                return
            if self.views.currentIndex() == 1:
                hyp_id, label = self._selected_hypothesis()
                if not hyp_id:
                    self._warn("Select a hypothesis in the Ranking tab to remove it."); return
                if QMessageBox.question(self, "Remove hypothesis",
                                        f"Remove “{label}” and all of its ratings?") \
                        != QMessageBox.StandardButton.Yes:
                    return
                self._mutate(lambda dest: core.remove_hypothesis(self.analysis, dest, self.secret, hyp_id), tab=1)
            else:
                ev_id, text = self._selected_evidence()
                if not ev_id:
                    self._warn("Select an evidence row in the Matrix tab to remove it."); return
                if QMessageBox.question(self, "Remove evidence", f"Remove “{text}”?") \
                        != QMessageBox.StandardButton.Yes:
                    return
                self._mutate(lambda dest: core.remove_evidence(self.analysis, dest, self.secret, ev_id), tab=0)

        def export_report(self):
            if not self.data:
                return
            choice, ok = QInputDialog.getItem(self, "Export report", "Format:",
                                              ["HTML report", "JSON report"], 0, False)
            if not ok:
                return
            fmt = "html" if choice.startswith("HTML") else "json"
            dest, _ = QFileDialog.getSaveFileName(self, "Export ACH report",
                                                  f"analysis-report.{fmt}", f"{fmt.upper()} (*.{fmt});;All files (*)")
            if not dest:
                return
            try:
                if Path(dest).exists():
                    Path(dest).unlink()
                result = core.export(self.analysis, dest, self.secret, fmt)
            except (HypothesisError, OSError) as exc:
                self._warn(str(exc)); return
            self._warn(f"Exported a plaintext {fmt.upper()} report to {result['output']}.\n\n"
                       f"SHA-256: {result['sha256']}", icon=QMessageBox.Icon.Information, title="Report exported")

        def _mutate(self, action, tab):
            dest = self._next_revision_path()
            try:
                action(dest)
            except (HypothesisError, OSError) as exc:
                self._warn(str(exc)); return
            self._reopen(dest)
            self.views.setCurrentIndex(tab)

        # ----- selection ----------------------------------------------------
        def _selected_evidence(self):
            row = self.t_matrix.currentRow()
            if row < 0:
                return None, None
            item = self.t_matrix.item(row, 0)
            if item is None or item.data(ID_ROLE) is None:
                return None, None
            return item.data(ID_ROLE), item.data(ID_ROLE + 1)

        def _selected_cell(self):
            row, col = self.t_matrix.currentRow(), self.t_matrix.currentColumn()
            ev_id, ev_text = self._selected_evidence()
            if not ev_id or col < 2 or (col - 2) >= len(self.hyp_ids):
                return None, None, None, None
            hyp_id = self.hyp_ids[col - 2]
            label = next((h["label"] for h in self.data["hypotheses"] if h["id"] == hyp_id), "")
            return ev_id, hyp_id, label, ev_text

        def _selected_hypothesis(self):
            row = self.t_rank.currentRow()
            if row < 0:
                return None, None
            item = self.t_rank.item(row, 1)
            if item is None or item.data(ID_ROLE) is None:
                return None, None
            return item.data(ID_ROLE), item.text()

        # ----- display ------------------------------------------------------
        def _refresh_all(self):
            self._refresh_header()
            self._fill_matrix()
            self._fill_ranking(self.report)
            self._fill_history(self.data["journal"])

        def _refresh_header(self):
            d, r = self.data, self.report
            if not d:
                return
            if r and r["leading"]:
                sep = r["separation"]
                decisive = "" if sep is None else (f" · separation {sep}" + ("" if r["decisive"] else " (not decisive)"))
                verdict = f"least contradicted: {r['leading']['label']}{decisive}"
            else:
                verdict = "add hypotheses and evidence, then rate the cells"
            self.mon_header.setPlainText(
                f"{d['title']}  ·  revision {d['revision']}  ·  {len(d['hypotheses'])} hypotheses  ·  "
                f"{len(d['evidence'])} evidence\nQ: {d['question'] or '—'}  ·  {verdict}")
            if self.analysis:
                self.path_label.setText(str(self.analysis))

        def _cell(self, text, color=None, align=None):
            item = QTableWidgetItem("" if text is None else str(text))
            if color:
                item.setForeground(QColor(color))
            if align:
                item.setTextAlignment(align)
            return item

        def _fill_matrix(self):
            hyps = self.data["hypotheses"]
            evidence = self.data["evidence"]
            self.hyp_ids = [h["id"] for h in hyps]
            t = self.t_matrix
            t.setColumnCount(2 + len(hyps))
            headers = ["Evidence", "Cred"] + [f"H{i + 1}" for i in range(len(hyps))]
            t.setHorizontalHeaderLabels(headers)
            for i, h in enumerate(hyps):
                hi = t.horizontalHeaderItem(2 + i)
                if hi is not None:
                    hi.setToolTip(h["label"])
            t.setRowCount(len(evidence))
            centre = int(Qt.AlignmentFlag.AlignCenter)
            for i, e in enumerate(evidence):
                ev_item = self._cell(e["text"], INK)
                ev_item.setData(ID_ROLE, e["id"]); ev_item.setData(ID_ROLE + 1, e["text"])
                if e["source"]:
                    ev_item.setToolTip(e["source"])
                t.setItem(i, 0, ev_item)
                t.setItem(i, 1, self._cell(e["credibility"], LAVENDER, centre))
                for j, h in enumerate(hyps):
                    v = e["ratings"].get(h["id"])
                    if v is None:
                        t.setItem(i, 2 + j, self._cell("·", DIM, centre))
                    else:
                        t.setItem(i, 2 + j, self._cell(RATING_GLYPH[v], RATING_COLOR[v], centre))
            t.resizeColumnsToContents(); t.horizontalHeader().setStretchLastSection(False)
            if t.columnCount() > 0:
                t.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            if not evidence:
                t.setRowCount(1)
                t.setItem(0, 0, self._cell("No evidence yet. Add hypotheses, then evidence, then rate the cells.", DIM))

        def _fill_ranking(self, report):
            rows = report["hypotheses"] if report else []
            t = self.t_rank; t.setRowCount(len(rows))
            for i, h in enumerate(rows):
                lead = h["rank"] == 1 and h["rated_evidence"] > 0
                color = GREEN if lead else INK
                t.setItem(i, 0, self._cell(f"#{h['rank']}", color))
                label_item = self._cell(h["label"], color)
                label_item.setData(ID_ROLE, h["id"])
                t.setItem(i, 1, label_item)
                t.setItem(i, 2, self._cell(h["weighted_inconsistency"], AMBER if h["weighted_inconsistency"] else DIM))
                t.setItem(i, 3, self._cell(h["weighted_consistency"], CYAN if h["weighted_consistency"] else DIM))
                t.setItem(i, 4, self._cell(h["rated_evidence"], MUTED))
            t.resizeColumnsToContents(); t.horizontalHeader().setStretchLastSection(True)
            if not rows:
                t.setRowCount(1)
                t.setItem(0, 0, self._cell("—", DIM))
                t.setItem(0, 1, self._cell("No hypotheses yet. Use “Add hypothesis”."))

        def _fill_history(self, journal):
            t = self.t_history; t.setRowCount(len(journal))
            palette = {"hypothesis_added": CYAN, "hypothesis_removed": DIM,
                       "evidence_added": LAVENDER, "evidence_removed": DIM, "rated": GREEN}
            for i, r in enumerate(reversed(journal)):
                t.setItem(i, 0, self._cell(r["recorded_utc"]))
                t.setItem(i, 1, self._cell(r["kind"], palette.get(r["kind"], INK)))
                t.setItem(i, 2, self._cell(r["detail"]))
            t.resizeColumnsToContents(); t.horizontalHeader().setStretchLastSection(True)

        def _next_revision_path(self):
            base = self.analysis
            stem = base.stem
            if ".r" in stem and stem.rsplit(".r", 1)[-1].isdigit():
                stem = stem.rsplit(".r", 1)[0]
            rev = (self.data["revision"] + 1) if self.data else 1
            candidate = base.with_name(f"{stem}.r{rev}{base.suffix}")
            n = rev
            while candidate.exists():
                n += 1
                candidate = base.with_name(f"{stem}.r{n}{base.suffix}")
            return candidate

        def _set_enabled(self, on):
            self.views.setEnabled(on)

        def _warn(self, message, icon=None, title="Hypothesis"):
            box = QMessageBox(self); box.setWindowTitle(title)
            box.setIcon(icon or QMessageBox.Icon.Warning); box.setText(message); box.exec()

        def _animate(self):
            self.phase += 0.05
            self.header.tick(self.phase)

    return QApplication, Window


STYLE = f"""
#body {{ background: {BG}; }}
QWidget {{ color: {INK}; font-family: Helvetica, Arial, sans-serif; font-size: 13px; }}
#status {{ color: {MUTED}; font-size: 12px; }}
#foot {{ color: {DIM}; font-size: 11px; }}
#mono {{ background: {PANEL_HI}; border: 1px solid {LINE}; border-radius: 9px; color: {INK};
         font-family: "Courier New", monospace; font-size: 13px; padding: 8px; }}
QTableWidget {{ background: {PANEL_HI}; alternate-background-color: {PANEL}; border: 1px solid {LINE};
                border-radius: 10px; gridline-color: {LINE}; }}
QTableWidget::item {{ padding: 4px 6px; }}
QTableWidget::item:selected {{ background: {VIOLET}; color: {INK}; }}
QHeaderView::section {{ background: {PANEL}; color: {MUTED}; padding: 6px 8px; border: none;
                        border-bottom: 1px solid {LINE}; font-weight: bold; }}
QTableCornerButton::section {{ background: {PANEL}; border: none; }}
QTabWidget#tabs::pane {{ border: 1px solid {LINE}; border-radius: 12px; background: {PANEL}; }}
QTabBar::tab {{ background: {PANEL_HI}; color: {MUTED}; padding: 8px 18px; margin-right: 6px;
                border-top-left-radius: 9px; border-top-right-radius: 9px; font-weight: bold; }}
QTabBar::tab:selected {{ color: {INK}; border-bottom: 2px solid {PINK}; }}
QPushButton#go {{ background: {PINK}; color: {BG}; border: none; border-radius: 10px; font-weight: bold; padding: 9px 14px; }}
QPushButton#go:hover {{ background: {CYAN}; }}
QPushButton#pick {{ background: {PANEL_HI}; border: 1px solid {LAVENDER}; border-radius: 9px; color: {INK};
                    font-weight: bold; padding: 9px 12px; }}
QPushButton#pick:hover {{ background: #1b2150; }}
"""


def run_app() -> int:
    try:
        from PyQt6.QtWidgets import QApplication  # noqa: F401
    except Exception:
        print("PyQt6 is required for the Hypothesis window. Use 'envy-hypothesis' subcommands instead.")
        return 2
    if sys.platform.startswith("linux") and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        print("No display found. Use 'envy-hypothesis init/hypothesis/evidence/rate/score/export' in a terminal instead.")
        return 2
    try:
        QApplication, Window = _build()
        app = QApplication.instance() or QApplication(sys.argv)
        window = Window(); window.show()
        return app.exec()
    except Exception as exc:  # pragma: no cover
        print(f"Could not open the window: {exc}")
        return 2
