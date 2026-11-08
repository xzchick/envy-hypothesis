"""Analysis of Competing Hypotheses (ACH): structure a judgement, don't guess it.

A self-contained engine for the ENVY BLACK VEIL Hypothesis app, packaged as a
standalone app. An *analysis* holds a key *question*, a set of competing
*hypotheses*, a set of *evidence* items (each with a credibility weight), and a
*rating* for each evidence/hypothesis cell — from very inconsistent to very
consistent. It is sealed in the same AES-256-GCM envelope as ENVY Vault.

A *score* read applies Richards Heuer's ACH method: it ranks hypotheses by the
weighted *inconsistency* of the evidence against them — the least-contradicted
hypothesis, not the most-supported one — and flags which evidence is actually
*diagnostic* (discriminates between hypotheses) versus evidence consistent with
everything, which decides nothing.

Every save is a new, hash-chained revision written with ``O_EXCL`` — an earlier
revision is never overwritten — and every change is recorded in a SHA-256
hash-chained journal.

Honest by design: ACH organizes your reasoning, it does not supply facts. The
ratings and weights are judgements you enter; the ranking is arithmetic on them.
A small separation between the top hypotheses means the evidence is not decisive,
and absence of evidence is not evidence.

Standard library only, apart from ``cryptography`` for the encrypted envelope.
"""

from __future__ import annotations

import datetime
import hashlib
import html
import json
import os
import stat
import uuid
from pathlib import Path

# --- Shared errors and bounds ------------------------------------------------

MAGIC = b"ENVYVAULT2\x00"
MAX_ENVELOPE = 16 * 1024 * 1024        # the encrypted analysis
MAX_HYPOTHESES = 50
MAX_EVIDENCE = 500
MAX_JOURNAL = 200000
MAX_REVISIONS = 1000000
MIN_PASSPHRASE = 12
MAX_LABEL = 300
MAX_STATEMENT = 1000
MAX_SOURCE = 2048
MAX_NOTE = 500
MIN_CREDIBILITY, MAX_CREDIBILITY = 1, 5

# The consistency scale for an evidence/hypothesis cell. A missing rating means
# "not applicable / not yet rated" and is simply absent from the matrix.
RATINGS = {
    2: "very consistent",
    1: "consistent",
    0: "neutral",
    -1: "inconsistent",
    -2: "very inconsistent",
}
# Friendly aliases accepted on input, in addition to the integers above.
RATING_ALIASES = {
    "cc": 2, "very-consistent": 2, "++": 2,
    "c": 1, "consistent": 1, "+": 1,
    "n": 0, "neutral": 0, "na": None, "none": None, "": None, "-clear": None,
    "i": -1, "inconsistent": -1, "-": -1,
    "ii": -2, "very-inconsistent": -2, "--": -2,
}


class HypothesisError(RuntimeError):
    """A clean, expected error: a bad passphrase, an invalid analysis, a chain mismatch."""


# --- Safe file primitives (never follow symlinks, never overwrite) -----------

def read_regular(path, limit: int = MAX_ENVELOPE) -> bytes:
    path = Path(path).expanduser()
    fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as stream:
        meta = os.fstat(stream.fileno())
        if not stat.S_ISREG(meta.st_mode) or meta.st_size > limit:
            raise HypothesisError("Expected a regular file within the size limit")
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise HypothesisError("File exceeded the size limit")
    return data


def new_file(path, data: bytes, mode: int = 0o600) -> Path:
    """Write a brand-new file. O_EXCL means an existing file is never overwritten."""
    path = Path(path).expanduser()
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    except FileExistsError:
        raise HypothesisError(f"Refusing to overwrite an existing file: {path}") from None
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    return path


# --- Encrypted envelope (byte-compatible with ENVY Vault) --------------------

def _key(secret: bytes, salt: bytes) -> bytes:
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    return Scrypt(salt=salt, length=32, n=2 ** 15, r=8, p=1).derive(secret)


def encrypt_bytes(data: bytes, secret: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt, nonce = os.urandom(16), os.urandom(12)
    return MAGIC + salt + nonce + AESGCM(_key(secret, salt)).encrypt(nonce, data, MAGIC)


def decrypt_bytes(data: bytes, secret: bytes) -> bytes:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if not data.startswith(MAGIC) or len(data) < len(MAGIC) + 44:
        raise HypothesisError("Not an ENVY encrypted envelope")
    blob = data[len(MAGIC):]
    try:
        return AESGCM(_key(secret, blob[:16])).decrypt(blob[16:28], blob[28:], MAGIC)
    except InvalidTag:
        raise HypothesisError("Incorrect passphrase or modified encrypted data") from None


def check_passphrase(secret: bytes) -> bytes:
    if not isinstance(secret, (bytes, bytearray)) or len(secret) < MIN_PASSPHRASE:
        raise HypothesisError(f"Use a passphrase of at least {MIN_PASSPHRASE} bytes")
    return bytes(secret)


# --- Canonical form and field validation -------------------------------------

def _canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _parse_utc(value, field="recorded_utc"):
    if not isinstance(value, str) or len(value) > 64:
        raise HypothesisError("Invalid " + field)
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HypothesisError(field + " must be ISO 8601 with a UTC offset") from None
    if parsed.tzinfo is None or parsed.utcoffset() != datetime.timedelta(0):
        raise HypothesisError(field + " must be expressed in UTC")
    return parsed


def _timestamp(value, field="recorded_utc"):
    return _parse_utc(value, field).isoformat()


def _text(value, field="text", limit=200, allow_empty=False):
    if not isinstance(value, str) or "\x00" in value or len(value) > limit:
        raise HypothesisError("Invalid " + field)
    if not allow_empty and not value.strip():
        raise HypothesisError("Invalid " + field)
    return value


def _uuid(value, field="identifier"):
    try:
        uuid.UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise HypothesisError("Invalid " + field) from None
    return value


def _sha256(value, field="sha256"):
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise HypothesisError(field + " must be 64 lowercase hexadecimal characters")
    return value


def _credibility(value):
    if type(value) is not int or not MIN_CREDIBILITY <= value <= MAX_CREDIBILITY:
        raise HypothesisError(f"Credibility must be a whole number from {MIN_CREDIBILITY} "
                              f"(weak) to {MAX_CREDIBILITY} (strong)")
    return value


def _rating(value):
    """Normalize a cell rating to an int in RATINGS, or None to clear it."""
    if value is None:
        return None
    if isinstance(value, str):
        key = value.strip().lower()
        if key in RATING_ALIASES:
            return RATING_ALIASES[key]
        try:
            value = int(key)
        except ValueError:
            raise HypothesisError("Rating must be one of very-consistent, consistent, neutral, "
                                  "inconsistent, very-inconsistent (or -2..2, or NA to clear)") from None
    if type(value) is not int or value not in RATINGS:
        raise HypothesisError("Rating must be a whole number from -2 (very inconsistent) to "
                              "2 (very consistent), or NA to clear")
    return value


# --- Hypothesis, evidence and journal validation -----------------------------

def _validate_hypothesis(value):
    if not isinstance(value, dict) or set(value) != {"id", "label", "created_utc"}:
        raise HypothesisError("Invalid hypothesis")
    return {"id": _uuid(value["id"], "hypothesis id"),
            "label": _text(value["label"], "hypothesis", MAX_LABEL),
            "created_utc": _timestamp(value["created_utc"], "created_utc")}


def _validate_evidence(value):
    fields = {"id", "text", "source", "credibility", "created_utc", "ratings"}
    if not isinstance(value, dict) or set(value) != fields:
        raise HypothesisError("Invalid evidence item")
    ratings = value["ratings"]
    if not isinstance(ratings, dict):
        raise HypothesisError("Evidence ratings must be a mapping")
    normalized = {}
    for hyp_id, cell in ratings.items():
        _uuid(hyp_id, "hypothesis id")
        if type(cell) is not int or cell not in RATINGS:
            raise HypothesisError("Each rating must be a whole number from -2 to 2")
        normalized[hyp_id] = cell
    return {"id": _uuid(value["id"], "evidence id"),
            "text": _text(value["text"], "evidence", MAX_STATEMENT),
            "source": _text(value["source"], "source", MAX_SOURCE, allow_empty=True),
            "credibility": _credibility(value["credibility"]),
            "created_utc": _timestamp(value["created_utc"], "created_utc"),
            "ratings": normalized}


def _record_digest(record):
    core = {k: v for k, v in record.items() if k != "record_sha256"}
    return hashlib.sha256(_canonical(core)).hexdigest()


_EVENT_KINDS = ("hypothesis_added", "hypothesis_removed",
                "evidence_added", "evidence_removed", "rated")


def _validate_record(value):
    fields = {"recorded_utc", "kind", "ref_id", "detail", "previous_sha256", "record_sha256"}
    if not isinstance(value, dict) or set(value) != fields:
        raise HypothesisError("Invalid journal record")
    if value["kind"] not in _EVENT_KINDS:
        raise HypothesisError("Invalid journal event kind")
    return {"recorded_utc": _timestamp(value["recorded_utc"]),
            "kind": value["kind"],
            "ref_id": _uuid(value["ref_id"], "reference id"),
            "detail": _text(value["detail"], "detail", 200, allow_empty=True),
            "previous_sha256": _sha256(value["previous_sha256"], "previous_sha256"),
            "record_sha256": _sha256(value["record_sha256"], "record_sha256")}


def _validate_analysis(data):
    fields = {"schema", "analysis_id", "title", "question", "created_utc", "revision",
              "previous_envelope_sha256", "hypotheses", "evidence", "journal"}
    if not isinstance(data, dict) or set(data) != fields or type(data["schema"]) is not int or data["schema"] != 1:
        raise HypothesisError("Unsupported encrypted analysis schema")
    _text(data["title"], "analysis title", 200)
    _text(data["question"], "question", MAX_STATEMENT, allow_empty=True)
    _uuid(data["analysis_id"], "analysis identifier")
    _timestamp(data["created_utc"], "created_utc")

    hyps = data["hypotheses"]
    if not isinstance(hyps, list) or len(hyps) > MAX_HYPOTHESES:
        raise HypothesisError("Invalid hypothesis list")
    hyp_ids, previous_id = set(), None
    for hyp in hyps:
        if _validate_hypothesis(hyp) != hyp:
            raise HypothesisError("Hypothesis is not normalized")
        if hyp["id"] in hyp_ids:
            raise HypothesisError("Duplicate hypothesis id")
        if previous_id is not None and hyp["id"] < previous_id:
            raise HypothesisError("Hypotheses must be sorted by id")
        hyp_ids.add(hyp["id"]); previous_id = hyp["id"]

    evidence = data["evidence"]
    if not isinstance(evidence, list) or len(evidence) > MAX_EVIDENCE:
        raise HypothesisError("Invalid evidence list")
    seen, previous_id = set(), None
    for item in evidence:
        if _validate_evidence(item) != item:
            raise HypothesisError("Evidence item is not normalized")
        if item["id"] in seen:
            raise HypothesisError("Duplicate evidence id")
        if previous_id is not None and item["id"] < previous_id:
            raise HypothesisError("Evidence must be sorted by id")
        if any(hyp_id not in hyp_ids for hyp_id in item["ratings"]):
            raise HypothesisError("A rating references a hypothesis that is not in this analysis")
        seen.add(item["id"]); previous_id = item["id"]

    journal = data["journal"]
    if not isinstance(journal, list) or len(journal) > MAX_JOURNAL:
        raise HypothesisError("Invalid journal")
    previous = "0" * 64
    for record in journal:
        if _validate_record(record) != record:
            raise HypothesisError("Journal record is not normalized")
        if record["previous_sha256"] != previous or _record_digest(record) != record["record_sha256"]:
            raise HypothesisError("Analysis journal hash chain mismatch")
        previous = record["record_sha256"]

    revision = data["revision"]
    if type(revision) is not int or not 0 <= revision <= MAX_REVISIONS:
        raise HypothesisError("Invalid revision")
    _sha256(data["previous_envelope_sha256"], "previous_envelope_sha256")
    return data


# --- Load / write ------------------------------------------------------------

def _load(source, secret):
    encrypted = read_regular(source, MAX_ENVELOPE)
    try:
        data = json.loads(decrypt_bytes(encrypted, secret))
    except (json.JSONDecodeError, UnicodeError, RecursionError):
        raise HypothesisError("Encrypted analysis does not contain valid JSON") from None
    return _validate_analysis(data), hashlib.sha256(encrypted).hexdigest()


def _write(destination, data, secret):
    check_passphrase(secret)
    encoded = _canonical(_validate_analysis(data))
    if len(encoded) + 128 > MAX_ENVELOPE:
        raise HypothesisError("Analysis exceeds the encrypted size limit")
    new_file(destination, encrypt_bytes(encoded, secret))
    return {"output": str(destination), "analysis_id": data["analysis_id"], "revision": data["revision"],
            "hypotheses": len(data["hypotheses"]), "evidence": len(data["evidence"]),
            "encrypted": True, "previous_revision_preserved": True}


def _append_event(data, kind, ref_id, detail):
    record = {"recorded_utc": _utc(), "kind": kind, "ref_id": ref_id,
              "detail": _text(detail, "detail", 200, allow_empty=True),
              "previous_sha256": data["journal"][-1]["record_sha256"] if data["journal"] else "0" * 64}
    record["record_sha256"] = _record_digest(record)
    data["journal"].append(_validate_record(record))


def _save_revision(data, previous_envelope, destination, secret):
    data["revision"] += 1
    data["previous_envelope_sha256"] = previous_envelope
    return _write(destination, data, secret)


def create(destination, title, secret, question=""):
    data = {"schema": 1, "analysis_id": str(uuid.uuid4()), "title": _text(title, "analysis title", 200),
            "question": _text(question, "question", MAX_STATEMENT, allow_empty=True),
            "created_utc": _utc(), "revision": 0, "previous_envelope_sha256": "0" * 64,
            "hypotheses": [], "evidence": [], "journal": []}
    return _write(destination, data, secret)


# --- Mutations (each writes a new, preserved revision) -----------------------

def add_hypothesis(source, destination, secret, label):
    data, envelope = _load(source, secret)
    if len(data["hypotheses"]) >= MAX_HYPOTHESES:
        raise HypothesisError(f"An analysis holds at most {MAX_HYPOTHESES} hypotheses")
    hyp = _validate_hypothesis({"id": str(uuid.uuid4()), "label": label, "created_utc": _utc()})
    data["hypotheses"] = sorted(data["hypotheses"] + [hyp], key=lambda h: h["id"])
    _append_event(data, "hypothesis_added", hyp["id"], label[:120])
    return dict(_save_revision(data, envelope, destination, secret), hypothesis_id=hyp["id"])


def remove_hypothesis(source, destination, secret, hypothesis_id):
    data, envelope = _load(source, secret)
    hypothesis_id = _uuid(hypothesis_id, "hypothesis id")
    kept = [h for h in data["hypotheses"] if h["id"] != hypothesis_id]
    if len(kept) == len(data["hypotheses"]):
        raise HypothesisError("No such hypothesis in this analysis")
    data["hypotheses"] = kept
    for item in data["evidence"]:               # drop this column from every evidence row
        item["ratings"].pop(hypothesis_id, None)
    _append_event(data, "hypothesis_removed", hypothesis_id, "")
    return dict(_save_revision(data, envelope, destination, secret), removed=hypothesis_id)


def add_evidence(source, destination, secret, text, source_text="", credibility=3):
    data, envelope = _load(source, secret)
    if len(data["evidence"]) >= MAX_EVIDENCE:
        raise HypothesisError(f"An analysis holds at most {MAX_EVIDENCE} evidence items")
    item = _validate_evidence({"id": str(uuid.uuid4()), "text": text, "source": source_text,
                               "credibility": credibility, "created_utc": _utc(), "ratings": {}})
    data["evidence"] = sorted(data["evidence"] + [item], key=lambda e: e["id"])
    _append_event(data, "evidence_added", item["id"], f"cred {credibility} — {text[:110]}")
    return dict(_save_revision(data, envelope, destination, secret), evidence_id=item["id"])


def remove_evidence(source, destination, secret, evidence_id):
    data, envelope = _load(source, secret)
    evidence_id = _uuid(evidence_id, "evidence id")
    kept = [e for e in data["evidence"] if e["id"] != evidence_id]
    if len(kept) == len(data["evidence"]):
        raise HypothesisError("No such evidence item in this analysis")
    data["evidence"] = kept
    _append_event(data, "evidence_removed", evidence_id, "")
    return dict(_save_revision(data, envelope, destination, secret), removed=evidence_id)


def reweight(source, destination, secret, evidence_id, credibility):
    data, envelope = _load(source, secret)
    evidence_id = _uuid(evidence_id, "evidence id")
    item = next((e for e in data["evidence"] if e["id"] == evidence_id), None)
    if item is None:
        raise HypothesisError("No such evidence item in this analysis")
    item["credibility"] = _credibility(credibility)
    _append_event(data, "rated", evidence_id, f"credibility {credibility}")
    return dict(_save_revision(data, envelope, destination, secret), evidence_id=evidence_id)


def rate(source, destination, secret, evidence_id, hypothesis_id, value):
    """Set, change or clear one evidence/hypothesis cell. value None (or 'NA') clears it."""
    data, envelope = _load(source, secret)
    evidence_id = _uuid(evidence_id, "evidence id")
    hypothesis_id = _uuid(hypothesis_id, "hypothesis id")
    item = next((e for e in data["evidence"] if e["id"] == evidence_id), None)
    if item is None:
        raise HypothesisError("No such evidence item in this analysis")
    if not any(h["id"] == hypothesis_id for h in data["hypotheses"]):
        raise HypothesisError("No such hypothesis in this analysis")
    rating = _rating(value)
    if rating is None:
        item["ratings"].pop(hypothesis_id, None)
        detail = "cleared"
    else:
        item["ratings"][hypothesis_id] = rating
        detail = RATINGS[rating]
    _append_event(data, "rated", evidence_id, f"{hypothesis_id[:8]} → {detail}")
    return dict(_save_revision(data, envelope, destination, secret),
                evidence_id=evidence_id, hypothesis_id=hypothesis_id, rating=rating)


# --- ACH scoring -------------------------------------------------------------

def score(source, secret):
    """Rank hypotheses by weighted inconsistency and flag diagnostic evidence. Read-only."""
    data, envelope = _load(source, secret)
    hyps, evidence = data["hypotheses"], data["evidence"]

    rows = []
    for h in hyps:
        wi = wc = rated = 0
        for e in evidence:
            cell = e["ratings"].get(h["id"])
            if cell is None:
                continue
            rated += 1
            if cell < 0:
                wi += e["credibility"] * -cell
            elif cell > 0:
                wc += e["credibility"] * cell
        rows.append({"id": h["id"], "label": h["label"], "weighted_inconsistency": wi,
                     "weighted_consistency": wc, "rated_evidence": rated})
    # ACH ranks by LEAST weighted inconsistency first; more consistency breaks ties.
    rows.sort(key=lambda r: (r["weighted_inconsistency"], -r["weighted_consistency"], r["label"]))
    for i, r in enumerate(rows):
        r["rank"] = i + 1

    leading = rows[0] if rows else None
    runner_up = rows[1] if len(rows) > 1 else None
    separation = (runner_up["weighted_inconsistency"] - leading["weighted_inconsistency"]) \
        if runner_up else None

    diagnosticity = []
    for e in evidence:
        present = [e["ratings"][h["id"]] for h in hyps if h["id"] in e["ratings"]]
        spread = (max(present) - min(present)) if len(present) >= 2 else 0
        diagnosticity.append({"id": e["id"], "text": e["text"], "credibility": e["credibility"],
                              "rated_hypotheses": len(present), "spread": spread,
                              "diagnosticity": spread * e["credibility"],
                              "diagnostic": spread > 0})
    diagnosticity.sort(key=lambda d: (-d["diagnosticity"], d["text"]))

    return {"analysis_id": data["analysis_id"], "title": data["title"], "question": data["question"],
            "revision": data["revision"], "evaluated_utc": _utc(), "envelope_sha256": envelope,
            "hypothesis_count": len(hyps), "evidence_count": len(evidence),
            "hypotheses": rows, "evidence_diagnosticity": diagnosticity,
            "leading": {"id": leading["id"], "label": leading["label"]} if leading else None,
            "separation": separation,
            "decisive": bool(separation) and separation > 0,
            "basis": "ACH ranks hypotheses by the weighted inconsistency of the evidence against them — "
                     "the least-contradicted hypothesis, not the most-supported. Diagnostic evidence is "
                     "evidence that discriminates between hypotheses; evidence consistent with everything "
                     "decides nothing. A small or zero separation between the top hypotheses means the "
                     "evidence is not decisive. Ratings and weights are your judgements, not facts."}


# --- Read-only views ---------------------------------------------------------

def matrix(source, secret):
    """The full ACH grid: hypotheses as columns, evidence as rows, cells as ratings. Read-only."""
    data, _ = _load(source, secret)
    return {"analysis_id": data["analysis_id"], "title": data["title"], "question": data["question"],
            "revision": data["revision"],
            "hypotheses": [{"id": h["id"], "label": h["label"]} for h in data["hypotheses"]],
            "evidence": [{"id": e["id"], "text": e["text"], "source": e["source"],
                          "credibility": e["credibility"],
                          "ratings": {hid: {"value": v, "label": RATINGS[v]}
                                      for hid, v in e["ratings"].items()}}
                         for e in data["evidence"]],
            "scale": RATINGS}


def inspect(source, secret):
    data, envelope = _load(source, secret)
    return {"analysis_id": data["analysis_id"], "title": data["title"], "question": data["question"],
            "revision": data["revision"], "created_utc": data["created_utc"], "envelope_sha256": envelope,
            "hypotheses": data["hypotheses"], "evidence": data["evidence"],
            "journal": [{"recorded_utc": r["recorded_utc"], "kind": r["kind"],
                         "ref_id": r["ref_id"], "detail": r["detail"]} for r in data["journal"]],
            "warning": "The journal is append-only and hash-chained, so changes cannot be reordered or edited "
                       "without breaking the chain — but the chain orders events, it does not prove when they "
                       "happened, and every rating and weight is your own judgement."}


# --- Plaintext export --------------------------------------------------------

def export(source, destination, secret, format="html"):
    if format not in ("html", "json"):
        raise HypothesisError("Export format must be html or json")
    data, envelope = _load(source, secret)
    report = score(source, secret)
    label_by_id = {h["id"]: h["label"] for h in data["hypotheses"]}
    out = {"analysis": {"analysis_id": data["analysis_id"], "title": data["title"],
                        "question": data["question"], "revision": data["revision"],
                        "created_utc": data["created_utc"]},
           "evaluated_utc": report["evaluated_utc"], "leading": report["leading"],
           "separation": report["separation"], "decisive": report["decisive"],
           "hypotheses": report["hypotheses"], "evidence_diagnosticity": report["evidence_diagnosticity"],
           "matrix": matrix(source, secret)["evidence"], "envelope_sha256": envelope,
           "limitations": "Plaintext export. ACH organizes your reasoning; it does not supply facts. The "
                          "ratings and weights are judgements you entered, the ranking is arithmetic on them, "
                          "and a small separation between the top hypotheses means the evidence is not decisive."}
    if format == "json":
        output = (json.dumps(out, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    else:
        def esc(v):
            return html.escape(str(v), quote=True)

        hyp_rows = "".join(
            "<tr><td>" + esc(h["rank"]) + "</td><td>" + esc(h["label"]) + "</td><td>" +
            esc(h["weighted_inconsistency"]) + "</td><td>" + esc(h["weighted_consistency"]) +
            "</td><td>" + esc(h["rated_evidence"]) + "</td></tr>" for h in report["hypotheses"])
        diag_rows = "".join(
            "<tr><td>" + esc(d["text"]) + "</td><td>" + esc(d["credibility"]) + "</td><td>" +
            esc(d["diagnosticity"]) + "</td><td>" + ("yes" if d["diagnostic"] else "no") +
            "</td></tr>" for d in report["evidence_diagnosticity"])
        lead = report["leading"]
        sep = report["separation"]
        headline = ("Add hypotheses and evidence to begin." if not lead else
                    "Least contradicted: " + esc(lead["label"]) +
                    (f" (separation {sep} — not yet decisive)" if sep is not None and sep <= 0
                     else f" (separation {sep})" if sep is not None else ""))
        # No external content, executable script, clickable operator-supplied URLs, or active HTML.
        output = ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
                  "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'\">"
                  "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
                  "<title>" + esc(data["title"]) + "</title><style>body{max-width:74rem;margin:2rem auto;padding:0 1rem;font:16px system-ui;line-height:1.5}"
                  "table{border-collapse:collapse;width:100%;margin:0 0 2rem}th,td{border:1px solid #777;padding:.4rem .6rem;text-align:left;vertical-align:top}"
                  "th{background:#eee}</style></head><body><h1>" + esc(data["title"]) +
                  "</h1><p><strong>Question:</strong> " + esc(data["question"] or "—") + "</p>"
                  "<p>" + esc(out["limitations"]) + "</p><p>Analysis: " + esc(data["analysis_id"]) +
                  " · Revision: " + str(data["revision"]) + " · Evaluated UTC: " + esc(report["evaluated_utc"]) +
                  " · Encrypted analysis SHA-256: " + envelope + "</p><h2>" + headline + "</h2>"
                  "<h3>Hypotheses, least contradicted first</h3>"
                  "<table><thead><tr><th>Rank</th><th>Hypothesis</th><th>Weighted inconsistency</th>"
                  "<th>Weighted consistency</th><th>Rated evidence</th></tr></thead><tbody>"
                  + (hyp_rows or "<tr><td colspan=\"5\">No hypotheses yet.</td></tr>") + "</tbody></table>"
                  "<h3>Evidence by diagnosticity</h3>"
                  "<table><thead><tr><th>Evidence</th><th>Credibility</th><th>Diagnosticity</th>"
                  "<th>Discriminates?</th></tr></thead><tbody>"
                  + (diag_rows or "<tr><td colspan=\"4\">No evidence yet.</td></tr>") + "</tbody></table>"
                  "</body></html>").encode("utf-8")
    new_file(destination, output)
    return {"output": str(destination), "format": format, "plaintext": True,
            "sha256": hashlib.sha256(output).hexdigest(),
            "leading": report["leading"], "separation": report["separation"]}
