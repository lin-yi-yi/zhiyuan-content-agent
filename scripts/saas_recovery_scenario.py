"""Synthetic HTTP acceptance scenario; no application imports or credential reports.

Transport(method, path, body=None, headers=None) returns status/body/headers.
Keep PrivateState in memory only. It intentionally contains no restored sessions.
"""
from copy import deepcopy
from dataclasses import dataclass, field
from functools import wraps
import hashlib
from http.cookies import SimpleCookie
import json
import re
import secrets
from urllib.parse import urlsplit


ORIGIN = "http://127.0.0.1:8765"
MARKERS = {"owner_a": "ORG-A-RECOVERY", "owner_b": "ORG-B-RECOVERY"}
EMBEDDING_MODEL = "zhiyuan-recovery-synthetic-3d-v1"


class ScenarioError(RuntimeError):
    """Fixed failure codes only: never response bodies, headers or credentials."""


def require(condition, code):
    if not condition:
        raise ScenarioError(code)


def bounded_errors(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except ScenarioError:
            raise
        except Exception:
            raise ScenarioError("INVALID_SCENARIO_RESPONSE") from None
    return wrapped


@dataclass(repr=False)
class Account:
    label: str
    email: str
    password: str
    organization_id: str = ""
    user_id: str = ""


@dataclass(repr=False)
class PrivateState:
    origin: str
    accounts: dict[str, Account] = field(default_factory=dict)
    documents: dict[str, list[dict]] = field(default_factory=dict)
    modified: bool = False


def digest(value):
    require(isinstance(value, str), "INVALID_TEXT_FIELD")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def identifier(value):
    require(isinstance(value, str) and re.fullmatch(r"[a-f0-9]{32}", value), "INVALID_IDENTITY")
    return value


def number(value):
    require(type(value) is int and value > 0, "INVALID_RECORD_ID")
    return value


class Scenario:
    """One fresh in-memory client. A transport must not add its own cookie jar."""
    def __init__(self, transport, origin=ORIGIN):
        parsed = urlsplit(origin)
        require(parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "testserver"}
                and not (parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment),
                "INVALID_SCENARIO_ORIGIN")
        self.transport, self.origin = transport, origin
        self.cookie = self.csrf = self.organization_id = ""

    def call(self, method, path, body=None, *, expected=200, headers=None):
        outgoing = {"origin": self.origin, "cookie": self.cookie}
        if self.csrf:
            outgoing["x-csrf-token"] = self.csrf
        if self.organization_id:
            outgoing["x-organization-id"] = self.organization_id
        outgoing.update({key.lower(): value for key, value in (headers or {}).items()})
        try:
            response = self.transport(method, path, body=body, headers=outgoing)
        except Exception:
            raise ScenarioError("HTTP_TRANSPORT_FAILED") from None
        require(isinstance(response, dict) and type(response.get("status")) is int, "INVALID_HTTP_RESPONSE")
        require(response["status"] == expected, "UNEXPECTED_HTTP_STATUS")
        if expected >= 400:
            return None  # Never copy a rejected response's arbitrary text into evidence.
        try:
            value = json.loads(response["body"])
            returned_headers = {key.lower(): value for key, value in response["headers"].items()}
        except Exception:
            raise ScenarioError("INVALID_HTTP_RESPONSE") from None
        require(isinstance(value, dict), "INVALID_HTTP_RESPONSE")
        if path in {"/api/saas/auth/login", "/api/saas/auth/register"}:
            try:
                cookies = SimpleCookie()
                cookies.load(returned_headers.get("set-cookie", ""))
                cookie = cookies["zhiyuan_session"].value
            except Exception:
                raise ScenarioError("MISSING_AUTH_COOKIE") from None
            require(bool(re.fullmatch(r"[A-Za-z0-9_-]{32,128}", cookie)), "INVALID_AUTH_COOKIE")
            csrf = value.get("csrf_token")
            require(isinstance(csrf, str) and re.fullmatch(r"[a-f0-9]{64}", csrf), "INVALID_CSRF")
            self.cookie, self.csrf = f"zhiyuan_session={cookie}", csrf
            self.organization_id = identifier(value.get("active_organization_id"))
        return value

    def adopt(self, account, value, role, *, registering=False):
        require(value.get("mode") == "saas" and value.get("authenticated") is True, "AUTHENTICATION_FAILED")
        organizations = value["organizations"]
        require(len(organizations) == 1 and organizations[0]["role"] == role
                and organizations[0]["id"] == self.organization_id, "MEMBERSHIP_MISMATCH")
        user_id = identifier(value["user"]["id"])
        if registering:
            account.organization_id, account.user_id = self.organization_id, user_id
        require(account.organization_id == self.organization_id and account.user_id == user_id, "IDENTITY_CHANGED")

    def login(self, account, role):
        require(not self.cookie and not self.csrf, "CLIENT_NOT_FRESH")
        value = self.call("POST", "/api/saas/auth/login", {"email": account.email, "password": account.password})
        self.adopt(account, value, role)


def document_body(label, *, added=False):
    marker = MARKERS[label] + ("-ADDED" if added else "")
    return {"title": marker, "content": marker + "。这是一份明确标记的合成组织资料，只用于验证停机备份恢复、真实向量存储及组织隔离。它不包含真实客户信息，也不是模型语义质量或产品规格的证据。",
            "format": "text", "source_uri": "demo://saas-recovery/" + label + ("/added" if added else "/initial")}


def upload(client, label, *, added=False):
    body = document_body(label, added=added)
    value = client.call("POST", "/api/knowledge/documents", body, expected=201)
    return {"id": number(value["document"]["id"]), **body}


def document_signature(client, expected):
    value = client.call("GET", f"/api/knowledge/documents/{expected['id']}")
    require(value["id"] == expected["id"] and value["status"] == "indexed", "DOCUMENT_STATE_CHANGED")
    require(value["title"] == expected["title"] and value["source_uri"] == expected["source_uri"]
            and value["content"] == expected["content"] and value["content_hash"] == digest(expected["content"]),
            "DOCUMENT_CONTENT_CHANGED")
    require(value["chunk_count"] == len(value["chunks"]) == 1, "CHUNK_COUNT_CHANGED")
    meta, chunk = value["metadata"], value["chunks"][0]
    require(meta["embedding_provider"] == chunk["embedding_provider"] == "synthetic"
            and meta["embedding_model"] == chunk["embedding_model"] == EMBEDDING_MODEL, "EMBEDDING_METADATA_CHANGED")
    require(isinstance(meta.get("index_fingerprint"), str)
            and re.fullmatch(r"[a-f0-9]{16}", meta["index_fingerprint"]), "INVALID_INDEX_FINGERPRINT")
    require(meta.get("vector_collection") == f"knowledge_{meta['index_fingerprint']}_3", "VECTOR_COLLECTION_MISSING")
    require(chunk["content"] == expected["content"] and chunk["chunk_index"] == 0, "CHUNK_CONTENT_CHANGED")
    return {"document_id": number(value["id"]), "workspace_id": number(value["workspace_id"]),
            "knowledge_base_id": number(value["knowledge_base_id"]), "content_hash": value["content_hash"],
            "chunk_count": 1, "chunk_id": number(chunk["id"]), "chunk_hash": digest(chunk["content"]),
            "embedding_provider": "synthetic", "embedding_model_sha256": digest(chunk["embedding_model"]),
            "index_fingerprint": meta["index_fingerprint"], "vector_collection": meta["vector_collection"]}


def usage_signature(client, document_count, member_count):
    value = client.call("GET", "/api/saas/billing")
    expected = {"used": 1, "reserved": 0, "settled": 1, "remaining": 99, "attempts": 1, "refunded": 0}
    require(value["plan"]["code"] == "trial" and value["usage"]["ai_requests"] == expected, "USAGE_CHANGED")
    require(value["usage"]["documents"] == {"used": document_count, "limit": 200, "measured": True}
            and value["usage"]["members"] == {"used": member_count, "limit": 3}, "CAPACITY_CHANGED")
    # A stable allowlist, never the account details, event history or notice text.
    return {"plan": "trial", "ai_requests": expected,
            "documents": {"used": document_count, "limit": 200}, "members": {"used": member_count, "limit": 3}}


def inspect_organization(client, state, label):
    expected_documents = state.documents[label]
    listing = client.call("GET", "/api/knowledge/documents")
    require(listing["total"] == len(listing["items"]) == len(expected_documents)
            and {item["id"] for item in listing["items"]} == {item["id"] for item in expected_documents}, "DOCUMENT_COUNT_CHANGED")
    documents = [document_signature(client, item) for item in expected_documents]
    other = MARKERS["owner_b" if label == "owner_a" else "owner_a"]
    expected_chunks = {item["chunk_id"]: item for item in documents}
    searches = {}
    for mode in ("semantic", "hybrid"):
        result = client.call("POST", "/api/v04/rag/search", {"query": MARKERS[label], "retrieval_mode": mode, "top_k": 12})
        require(result["total"] == len(result["items"]) == len(documents), "SEARCH_COUNT_CHANGED")
        found = []
        for hit in result["items"]:
            require(hit["chunk_id"] in expected_chunks, "SEARCH_CROSSED_SCOPE")
            expected = expected_chunks[hit["chunk_id"]]
            require(hit["document_id"] == expected["document_id"] and hit["workspace_id"] == expected["workspace_id"]
                    and hit["knowledge_base_id"] == expected["knowledge_base_id"]
                    and digest(hit["content"]) == expected["chunk_hash"]
                    and MARKERS[label] in hit["content"] and other not in hit["content"], "SEARCH_CROSSED_SCOPE")
            require(hit["metadata"]["retrieval_mode"] == mode, "RETRIEVAL_MODE_CHANGED")
            found.append({"document_id": hit["document_id"], "chunk_id": hit["chunk_id"], "chunk_hash": expected["chunk_hash"]})
        require(len({item["chunk_id"] for item in found}) == len(documents), "SEARCH_DUPLICATE_CHUNK")
        searches[mode] = sorted(found, key=lambda item: item["chunk_id"])
    members = client.call("GET", "/api/saas/members")["items"]
    expected_members = {state.accounts[label].user_id: "owner"}
    if label == "owner_a":
        expected_members[state.accounts["viewer"].user_id] = "viewer"
    require(len(members) == len(expected_members)
            and {item["user_id"]: item["role"] for item in members if item["is_active"] is True} == expected_members,
            "MEMBERSHIP_CHANGED")
    model_runs = client.call("GET", "/api/models/runs")["runs"]
    require(len(model_runs) == 1 and model_runs[0]["provider"] == "local", "EXTERNAL_OR_EXTRA_MODEL_RUN")
    return {"organization_id": state.accounts[label].organization_id, "documents": documents,
            "searches": searches, "roles": sorted(expected_members.values()),
            "usage": usage_signature(client, len(documents), len(members)), "recorded_external_model_runs": 0}


@bounded_errors
def seed(transport, *, origin=ORIGIN):
    """Create exactly two synthetic organizations and one invited viewer."""
    state = PrivateState(origin)
    Scenario(transport, origin).call("GET", "/api/knowledge/documents", expected=401)
    clients = {}
    for label in ("owner_a", "owner_b", "viewer"):
        state.accounts[label] = Account(label, f"recovery-{label}-{secrets.token_hex(8)}@example.invalid",
                                        "Synthetic-Recovery-" + secrets.token_urlsafe(24))
    for label in MARKERS:
        account = state.accounts[label]
        client = clients[label] = Scenario(transport, origin)
        value = client.call("POST", "/api/saas/auth/register", {"name": label, "email": account.email,
            "password": account.password, "organization_name": "Synthetic recovery " + label}, expected=201)
        client.adopt(account, value, "owner", registering=True)
    viewer = state.accounts["viewer"]
    invitation = clients["owner_a"].call("POST", "/api/saas/invites", {"email": viewer.email, "role": "viewer"}, expected=201)
    client = Scenario(transport, origin)
    value = client.call("POST", "/api/saas/auth/register", {"name": "Synthetic viewer", "email": viewer.email,
        "password": viewer.password, "invite_token": invitation["token"]}, expected=201)
    client.adopt(viewer, value, "viewer", registering=True)
    require(viewer.organization_id == state.accounts["owner_a"].organization_id
            != state.accounts["owner_b"].organization_id, "ORGANIZATIONS_NOT_ISOLATED")
    for label, client in clients.items():
        state.documents[label] = [upload(client, label)]
        client.call("POST", "/api/models/chat", {"provider": "local", "user_prompt": "Synthetic recovery quota attempt"})
    return state, verify(transport, state)


@bounded_errors
def verify(transport, state, *, origin=None):
    """Fresh logins; searches and rejected mutations must not change AI usage."""
    origin = state.origin if origin is None else origin
    Scenario(transport, origin).call("GET", "/api/knowledge/documents", expected=401)
    clients = {}
    for label, account in state.accounts.items():
        client = clients[label] = Scenario(transport, origin)
        client.login(account, "viewer" if label == "viewer" else "owner")
    for label, other in (("owner_a", "owner_b"), ("owner_b", "owner_a")):
        clients[label].call("GET", "/api/knowledge/documents", expected=403,
                            headers={"X-Organization-ID": state.accounts[other].organization_id})
    clients["owner_a"].call("POST", "/api/knowledge/documents", document_body("owner_a"), expected=403,
                            headers={"X-CSRF-Token": ""})
    clients["viewer"].call("POST", "/api/knowledge/documents", document_body("owner_a"), expected=403)
    clients["viewer"].call("GET", "/api/knowledge/documents", expected=403,
                           headers={"X-Organization-ID": state.accounts["owner_b"].organization_id})
    before = {label: usage_signature(clients[label], len(state.documents[label]), 2 if label == "owner_a" else 1)
              for label in MARKERS}
    organizations = {label: inspect_organization(clients[label], state, label) for label in MARKERS}
    first, second = (organizations[label]["documents"][0] for label in MARKERS)
    require(all(first[key] == second[key] for key in ("document_id", "chunk_id", "workspace_id", "knowledge_base_id")),
            "SYNTHETIC_IDS_DO_NOT_COLLIDE")
    require(first["content_hash"] != second["content_hash"], "SYNTHETIC_CONTENT_NOT_DISTINCT")
    require(all(before[label] == organizations[label]["usage"] for label in MARKERS), "VERIFICATION_CHANGED_USAGE")
    viewer_documents = clients["viewer"].call("GET", "/api/knowledge/documents")
    require(viewer_documents["total"] == len(viewer_documents["items"]) == len(state.documents["owner_a"])
            and {item["id"] for item in viewer_documents["items"]} == {item["id"] for item in state.documents["owner_a"]},
            "VIEWER_READ_SCOPE_CHANGED")
    viewer_signatures = [document_signature(clients["viewer"], item) for item in state.documents["owner_a"]]
    require(viewer_signatures == organizations["owner_a"]["documents"], "VIEWER_READ_SCOPE_CHANGED")
    return {"schema_version": 1, "organizations": organizations,
            "checks": {"fresh_login": True, "anonymous_rejected": True, "cross_organization_rejected": True,
                       "csrf_rejected": True, "viewer_mutation_rejected": True, "colliding_ids_isolated": True}}


@bounded_errors
def mutate(transport, state, *, origin=None):
    """Add one marker to the original volume; preserve the pre-backup expectation."""
    require(not state.modified, "SCENARIO_ALREADY_MUTATED")
    updated = deepcopy(state)
    updated.origin = state.origin if origin is None else origin
    client = Scenario(transport, updated.origin)
    client.login(state.accounts["owner_a"], "owner")
    updated.documents["owner_a"].append(upload(client, "owner_a", added=True))
    updated.modified = True
    return updated, verify(transport, updated)
