#!/usr/bin/env python3
"""A small, stateful stand-in for the parts of GitHub the skill uses.

The reconciler talks to GitHub only through the `gh` CLI.  Scenario tests put
this file on PATH as `gh`, so the real command runs unchanged against a JSON
state file.  Response shapes follow what GitHub returns for the queries the
reconciler sends; Project view filters are evaluated only for the forms the
skill writes, and any other filter fails loudly rather than guessing.

Live behaviour this simulation was calibrated against (read-only probes of a
real Project, 2026-09-25): `has:"Work phase"` returns exactly the items that
have a Work phase value; the unquoted `has:Work phase` returns nothing; a
`parent-issue:OWNER/REPO#N` filter returns N's direct sub-issues.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
from typing import Any


STATE_ENV = "FAKE_GH_STATE"


# --------------------------------------------------------------------- state

def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def save(path: Path, state: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def new_state(login: str = "builder", owner: str = "acme", owner_type: str = "User") -> dict[str, Any]:
    return {
        "login": login,
        "owner": {"login": owner, "type": owner_type, "id": f"OWNER_{owner}"},
        "repositories": {},
        "projects": [],
        "next_id": 1000,
        "rate": {"graphql": 5000, "core": 5000},
        "mutations": 0,
        "crash_at": None,
        "calls": [],
    }


def next_id(state: dict[str, Any]) -> int:
    state["next_id"] += 1
    return state["next_id"]


def add_repository(state: dict[str, Any], name: str) -> dict[str, Any]:
    repo = {"name": name, "id": f"R_{name.replace('/', '_')}", "issues": []}
    state["repositories"][name] = repo
    return repo


def add_issue(
    state: dict[str, Any],
    repository: str,
    title: str,
    *,
    work_key: str | None = None,
    parent: int | None = None,
    body: str | None = None,
    labels: list[str] | None = None,
) -> int:
    repo = state["repositories"][repository]
    number = len(repo["issues"]) + 1
    if body is None:
        body = f"Human description of {title}.\n"
        if work_key:
            body += (
                "\n<!-- work-accountability:begin -->\n"
                f"<!-- work-accountability:key {work_key} -->\n"
                "Storage profile: `project-fields`\n"
                "<!-- work-accountability:end -->\n"
            )
    database_id = next_id(state)
    repo["issues"].append(
        {
            "number": number,
            "id": database_id,
            "node_id": f"I_{database_id}",
            "title": title,
            "body": body,
            "state": "open",
            "labels": labels or [],
            "parent": parent,
        }
    )
    return number


def issue(state: dict[str, Any], repository: str, number: int) -> dict[str, Any]:
    for candidate in state["repositories"][repository]["issues"]:
        if candidate["number"] == number:
            return candidate
    raise NotFound()


def add_project(
    state: dict[str, Any],
    title: str,
    *,
    readme: str = "",
    repositories: list[str] | None = None,
) -> dict[str, Any]:
    number = max((p["number"] for p in state["projects"]), default=0) + 1
    ident = next_id(state)
    project = {
        "id": f"PVT_{ident}",
        "number": number,
        "title": title,
        "readme": readme,
        "shortDescription": "",
        "closed": False,
        "creator": state["login"],
        "createdAt": "2026-09-01T00:00:00Z",
        "repositories": list(repositories or []),
        "fields": [],
        "views": [],
        "items": [],
        "next_view": 1,
    }
    for name, data_type in (("Title", "TITLE"), ("Assignees", "ASSIGNEES")):
        add_field(state, project, name, data_type)
    add_field(state, project, "Status", "SINGLE_SELECT", [("Todo", ""), ("In Progress", ""), ("Done", "")])
    add_view(state, project, "View 1", "TABLE_LAYOUT")
    state["projects"].append(project)
    return project


def add_field(
    state: dict[str, Any],
    project: dict[str, Any],
    name: str,
    data_type: str,
    options: list[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    ident = next_id(state)
    field = {
        "id": f"PVTF_{ident}",
        "databaseId": ident,
        "name": name,
        "dataType": data_type,
        "options": [
            {"id": f"opt_{next_id(state)}", "name": option, "color": "GRAY", "description": description}
            for option, description in options or []
        ],
    }
    project["fields"].append(field)
    return field


def add_view(
    state: dict[str, Any],
    project: dict[str, Any],
    name: str,
    layout: str,
    *,
    filter: str | None = None,
    vertical: list[str] | None = None,
    group: list[str] | None = None,
    sort: list[list[str]] | None = None,
    visible: list[str] | None = None,
) -> dict[str, Any]:
    view = {
        "id": f"PVTV_{next_id(state)}",
        "number": project["next_view"],
        "name": name,
        "layout": layout,
        "filter": filter,
        "vertical": vertical or [],
        "group": group or [],
        "sort": sort or [],
        "visible": visible or [],
    }
    project["next_view"] += 1
    project["views"].append(view)
    return view


def add_item(state: dict[str, Any], project: dict[str, Any], repository: str, number: int) -> dict[str, Any]:
    item = {"id": f"PVTI_{next_id(state)}", "repository": repository, "number": number, "archived": False, "values": {}}
    project["items"].append(item)
    return item


def field_by_name(project: dict[str, Any], name: str) -> dict[str, Any]:
    return next(field for field in project["fields"] if field["name"] == name)


def set_value(project: dict[str, Any], item: dict[str, Any], name: str, value: Any) -> None:
    field = field_by_name(project, name)
    if field["dataType"] == "SINGLE_SELECT":
        option = next(o for o in field["options"] if o["name"] == value)
        item["values"][field["id"]] = option["id"]
    else:
        item["values"][field["id"]] = value


def project_by(state: dict[str, Any], *, number: int | None = None, ident: str | None = None) -> dict[str, Any]:
    for project in state["projects"]:
        if (number is not None and project["number"] == number) or (ident is not None and project["id"] == ident):
            return project
    raise NotFound()


# --------------------------------------------------------- what a user sees

def item_value(project: dict[str, Any], item: dict[str, Any], field: dict[str, Any]) -> Any:
    raw = item["values"].get(field["id"])
    if raw is None:
        return None
    if field["dataType"] == "SINGLE_SELECT":
        for option in field["options"]:
            if option["id"] == raw:
                return option["name"]
        return None
    return raw


def filter_items(state: dict[str, Any], project: dict[str, Any], query: str | None) -> list[dict[str, Any]]:
    items = [item for item in project["items"] if not item["archived"]]
    if not query:
        return items
    match = re.fullmatch(r'has:"([^"]+)"', query)
    if match:
        field = next((f for f in project["fields"] if f["name"] == match.group(1)), None)
        if field is None:
            return []
        return [item for item in items if item_value(project, item, field) is not None]
    if re.fullmatch(r"has:\S+ \S+", query):
        return []  # GitHub treats the second word as free text; nothing matches.
    match = re.fullmatch(r"parent-issue:(\S+)#(\d+)", query)
    if match:
        repository, parent = match.group(1), int(match.group(2))
        found = []
        for item in items:
            if item["repository"] != repository:
                continue
            try:
                if issue(state, repository, item["number"])["parent"] == parent:
                    found.append(item)
            except NotFound:
                continue
        return found
    raise SimulationError(f"github_sim does not evaluate Project filter {query!r}")


def render_view(state: dict[str, Any], project_number: int, view_name: str) -> dict[str, list[int]]:
    """Return the cards a person sees, keyed by column (board) or group (table)."""
    project = project_by(state, number=project_number)
    view = next(v for v in project["views"] if v["name"] == view_name)
    grouping = view["vertical"] if view["layout"] == "BOARD_LAYOUT" else view["group"]
    field = next((f for f in project["fields"] if f["id"] in grouping), None)
    sort_fields = [next(f for f in project["fields"] if f["id"] == fid) for fid, _d in view["sort"]]
    shown = filter_items(state, project, view["filter"])

    def order(item: dict[str, Any]) -> tuple:
        key = []
        for sort_field in sort_fields:
            value = item_value(project, item, sort_field)
            if sort_field["dataType"] == "SINGLE_SELECT":
                names = [o["name"] for o in sort_field["options"]]
                key.append(names.index(value) if value in names else len(names))
            else:
                key.append(float("inf") if value is None else value)
        return (*key, item["number"])

    columns: dict[str, list[int]] = {}
    for item in sorted(shown, key=order):
        column = item_value(project, item, field) if field else None
        columns.setdefault(column if column is not None else f"No {field['name'] if field else 'value'}", []).append(item["number"])
    return columns


# ------------------------------------------------------------------ helpers

class NotFound(Exception):
    pass


class SimulationError(Exception):
    pass


def summary(project: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": project["id"],
        "number": project["number"],
        "title": project["title"],
        "url": f"https://github.com/users/{state['owner']['login']}/projects/{project['number']}",
        "readme": project["readme"],
        "shortDescription": project["shortDescription"],
        "closed": project["closed"],
        "createdAt": project["createdAt"],
        "creator": {"login": project["creator"]},
        "repositories": {"nodes": [{"nameWithOwner": r} for r in project["repositories"]], "pageInfo": {"hasNextPage": False}},
    }


def field_ref(field: dict[str, Any]) -> dict[str, Any]:
    return {"id": field["id"], "name": field["name"]}


def field_node(field: dict[str, Any]) -> dict[str, Any]:
    node = {
        "__typename": "ProjectV2SingleSelectField" if field["dataType"] == "SINGLE_SELECT" else "ProjectV2Field",
        "id": field["id"],
        "databaseId": field["databaseId"],
        "name": field["name"],
        "dataType": field["dataType"],
    }
    if field["dataType"] == "SINGLE_SELECT":
        node["options"] = field["options"]
    return node


def view_node(project: dict[str, Any], view: dict[str, Any]) -> dict[str, Any]:
    fields = {field["id"]: field for field in project["fields"]}
    return {
        "id": view["id"],
        "number": view["number"],
        "name": view["name"],
        "layout": view["layout"],
        "filter": view["filter"],
        "verticalGroupByFields": {"nodes": [field_ref(fields[i]) for i in view["vertical"] if i in fields]},
        "groupByFields": {"nodes": [field_ref(fields[i]) for i in view["group"] if i in fields]},
        "fields": {"nodes": [field_ref(fields[i]) for i in view["visible"] if i in fields]},
        "sortByFields": {"nodes": [{"direction": d, "field": field_ref(fields[i])} for i, d in view["sort"] if i in fields]},
    }


def content_node(state: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    if item.get("draft"):
        return {"__typename": "DraftIssue", "title": item["draft"]}
    record = issue(state, item["repository"], item["number"])
    return {
        "__typename": "Issue",
        "id": record["node_id"],
        "number": record["number"],
        "repository": {"nameWithOwner": item["repository"]},
    }


def item_node(state: dict[str, Any], project: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    values = []
    for field in project["fields"]:
        value = item_value(project, item, field)
        if field["name"] == "Title" and not item.get("draft"):
            values.append({"__typename": "ProjectV2ItemFieldTextValue", "text": issue(state, item["repository"], item["number"])["title"], "field": field_ref(field)})
            continue
        if value is None:
            continue
        if field["dataType"] == "SINGLE_SELECT":
            values.append({"__typename": "ProjectV2ItemFieldSingleSelectValue", "name": value, "optionId": item["values"][field["id"]], "field": field_ref(field)})
        elif field["dataType"] == "NUMBER":
            values.append({"__typename": "ProjectV2ItemFieldNumberValue", "number": float(value), "field": field_ref(field)})
        elif field["dataType"] == "TEXT":
            values.append({"__typename": "ProjectV2ItemFieldTextValue", "text": value, "field": field_ref(field)})
    return {
        "id": item["id"],
        "isArchived": item["archived"],
        "content": content_node(state, item),
        "fieldValues": {"nodes": values, "pageInfo": {"hasNextPage": False}},
    }


def issue_rest(state: dict[str, Any], repository: str, record: dict[str, Any]) -> dict[str, Any]:
    children = [i for i in state["repositories"][repository]["issues"] if i["parent"] == record["number"]]
    return {
        "number": record["number"],
        "id": record["id"],
        "node_id": record["node_id"],
        "title": record["title"],
        "body": record["body"],
        "state": record["state"],
        "html_url": f"https://github.com/{repository}/issues/{record['number']}",
        "repository_url": f"https://api.github.com/repos/{repository}",
        "labels": [{"name": label} for label in record["labels"]],
        "sub_issues_summary": {"total": len(children), "completed": 0, "percent_completed": 0},
    }


# --------------------------------------------------------------------- REST

def rest(state: dict[str, Any], method: str, endpoint: str, data: Any) -> Any:
    path, _, query = endpoint.partition("?")
    params = dict(part.split("=", 1) for part in query.split("&") if "=" in part)
    if path == "user":
        return {"login": state["login"]}
    if path == "rate_limit":
        return {
            "resources": {
                "graphql": {"limit": 5000, "used": 5000 - state["rate"]["graphql"], "remaining": state["rate"]["graphql"], "reset": 1790000000},
                "core": {"limit": 5000, "used": 5000 - state["rate"]["core"], "remaining": state["rate"]["core"], "reset": 1790000000},
            }
        }
    match = re.fullmatch(r"repos/([^/]+/[^/]+)/issues", path)
    if match and method == "GET":
        repository = match.group(1)
        page = int(params.get("page", 1))
        per_page = int(params.get("per_page", 30))
        records = state["repositories"][repository]["issues"]
        chunk = records[(page - 1) * per_page : page * per_page]
        return [issue_rest(state, repository, record) for record in chunk]
    match = re.fullmatch(r"repos/([^/]+/[^/]+)/issues/(\d+)", path)
    if match:
        repository, number = match.group(1), int(match.group(2))
        record = issue(state, repository, number)
        if method == "PATCH":
            count_mutation(state)
            if "body" in data:
                record["body"] = data["body"]
            if "labels" in data:
                record["labels"] = list(data["labels"])
        return issue_rest(state, repository, record)
    match = re.fullmatch(r"repos/([^/]+/[^/]+)/issues/(\d+)/sub_issues", path)
    if match:
        repository, number = match.group(1), int(match.group(2))
        issue(state, repository, number)
        if method == "POST":
            count_mutation(state)
            child = next(i for i in state["repositories"][repository]["issues"] if i["id"] == data["sub_issue_id"])
            if child["parent"] is not None:
                raise SimulationError("Sub-issue already has a parent (HTTP 422)")
            child["parent"] = number
            return issue_rest(state, repository, issue(state, repository, number))
        page = int(params.get("page", 1))
        per_page = int(params.get("per_page", 30))
        children = [i for i in state["repositories"][repository]["issues"] if i["parent"] == number]
        return [issue_rest(state, repository, c) for c in children[(page - 1) * per_page : page * per_page]]
    match = re.fullmatch(r"repos/([^/]+/[^/]+)/issues/(\d+)/parent", path)
    if match:
        repository, number = match.group(1), int(match.group(2))
        record = issue(state, repository, number)
        if record["parent"] is None:
            raise NotFound()
        return issue_rest(state, repository, issue(state, repository, record["parent"]))
    match = re.fullmatch(r"(users|orgs)/([^/]+)/projectsV2/(\d+)/views", path)
    if match and method == "POST":
        count_mutation(state)
        project = project_by(state, number=int(match.group(3)))
        by_db = {field["databaseId"]: field["id"] for field in project["fields"]}
        view = add_view(
            state,
            project,
            data["name"],
            {"board": "BOARD_LAYOUT", "table": "TABLE_LAYOUT"}[data["layout"]],
            filter=data.get("filter"),
            vertical=[by_db[i] for i in data.get("vertical_group_by", [])],
            group=[by_db[i] for i in data.get("group_by", [])],
            sort=[[by_db[i], d.upper()] for i, d in data.get("sort_by", [])],
            visible=[by_db[i] for i in data.get("visible_fields", [])],
        )
        return {"id": view["id"], "number": view["number"], "name": view["name"]}
    raise SimulationError(f"github_sim has no REST route for {method} {endpoint}")


# ------------------------------------------------------------------ GraphQL

MUTATION_CALL = re.compile(r"(\w+):(\w+)\(input:\$(\w+)\)")


def graphql(state: dict[str, Any], query: str, variables: dict[str, Any]) -> dict[str, Any]:
    state["rate"]["graphql"] -= 1
    stripped = query.strip()
    if stripped.startswith("mutation"):
        data: dict[str, Any] = {}
        for alias, name, variable in MUTATION_CALL.findall(query):
            count_mutation(state)
            data[alias] = mutate(state, name, variables[variable])
        return data
    rate = {"rateLimit": {"cost": 1, "remaining": state["rate"]["graphql"], "resetAt": "2026-09-25T00:00:00Z"}}
    if re.search(r"i\d+:issue\(number:", query):
        repository = f"{variables['owner']}/{variables['name']}"
        result: dict[str, Any] = {}
        for number in map(int, re.findall(r"i(\d+):issue\(number:", query)):
            nodes = [
                {"id": item["id"], "project": {"id": project["id"]}}
                for project in state["projects"]
                for item in project["items"]
                if item["repository"] == repository and item["number"] == number
            ]
            result[f"i{number}"] = {"projectItems": {"nodes": nodes, "pageInfo": {"hasNextPage": False}}}
        return {"repository": result, **rate}
    if "repository(owner:$owner,name:$name)" in query:
        repository = f"{variables['owner']}/{variables['name']}"
        repo = state["repositories"][repository]
        linked = [summary(p, state) for p in state["projects"] if repository in p["repositories"]]
        return {
            "repository": {
                "id": repo["id"],
                "nameWithOwner": repository,
                "owner": {"__typename": state["owner"]["type"], "id": state["owner"]["id"], "login": state["owner"]["login"]},
                "projectsV2": {"nodes": linked, "pageInfo": {"hasNextPage": False}},
            },
            **rate,
        }
    owner_field = "organization" if "organization(login" in query else "user"
    if "projectsV2(first:100" in query:
        nodes = [summary(p, state) for p in state["projects"]]
        return {owner_field: {"projectsV2": {"nodes": nodes, "pageInfo": {"hasNextPage": False, "endCursor": None}}}, **rate}
    if "projectV2(number:$number)" in query:
        project = project_by(state, number=variables["number"])
        if "query:$q" in query:
            items = filter_items(state, project, variables.get("q"))
            nodes = [{"id": item["id"], "content": content_node(state, item)} for item in items]
            return {owner_field: {"projectV2": {"items": {"nodes": nodes, "pageInfo": {"hasNextPage": False, "endCursor": None}}}}, **rate}
        body = summary(project, state)
        body["fields"] = {"nodes": [field_node(f) for f in project["fields"]], "pageInfo": {"hasNextPage": False, "endCursor": None}}
        body["views"] = {"nodes": [view_node(project, v) for v in project["views"]], "pageInfo": {"hasNextPage": False}}
        body["items"] = {"nodes": [item_node(state, project, i) for i in project["items"]], "pageInfo": {"hasNextPage": False, "endCursor": None}}
        return {owner_field: {"projectV2": body}, **rate}
    raise SimulationError("github_sim does not recognise this GraphQL query:\n" + query)


def mutate(state: dict[str, Any], name: str, payload: dict[str, Any]) -> dict[str, Any]:
    if name == "createProjectV2":
        project = add_project(state, payload["title"])
        if payload.get("repositoryId"):
            repository = next(r["name"] for r in state["repositories"].values() if r["id"] == payload["repositoryId"])
            project["repositories"].append(repository)
        return {"projectV2": summary(project, state)}
    if name == "updateProjectV2":
        project = project_by(state, ident=payload["projectId"])
        for key in ("title", "readme", "closed", "shortDescription"):
            if key in payload:
                project[key] = payload[key]
        return {"projectV2": summary(project, state)}
    if name == "linkProjectV2ToRepository":
        project = project_by(state, ident=payload["projectId"])
        repository = next(r["name"] for r in state["repositories"].values() if r["id"] == payload["repositoryId"])
        if repository not in project["repositories"]:
            project["repositories"].append(repository)
        return {"repository": {"id": payload["repositoryId"], "nameWithOwner": repository}}
    if name == "createProjectV2Field":
        project = project_by(state, ident=payload["projectId"])
        if any(f["name"] == payload["name"] for f in project["fields"]):
            raise SimulationError(f"Name has already been taken: {payload['name']}")
        field = add_field(state, project, payload["name"], payload["dataType"])
        field["options"] = [
            {"id": f"opt_{next_id(state)}", "name": o["name"], "color": o.get("color", "GRAY"), "description": o.get("description", "")}
            for o in payload.get("singleSelectOptions", [])
        ]
        return {"projectV2Field": {"__typename": "ProjectV2Field", "id": field["id"], "name": field["name"]}}
    if name == "updateProjectV2Field":
        for project in state["projects"]:
            for field in project["fields"]:
                if field["id"] != payload["fieldId"]:
                    continue
                options = []
                for option in payload["singleSelectOptions"]:
                    options.append(
                        {
                            "id": option.get("id") or f"opt_{next_id(state)}",
                            "name": option["name"],
                            "color": option.get("color", "GRAY"),
                            "description": option.get("description", ""),
                        }
                    )
                kept = {o["id"] for o in options}
                for item in project["items"]:
                    if item["values"].get(field["id"]) and item["values"][field["id"]] not in kept:
                        del item["values"][field["id"]]  # GitHub clears values of removed options
                field["options"] = options
                return {"projectV2Field": {"id": field["id"], "name": field["name"], "options": options}}
        raise NotFound()
    if name == "addProjectV2ItemById":
        project = project_by(state, ident=payload["projectId"])
        for repository, repo in state["repositories"].items():
            for record in repo["issues"]:
                if record["node_id"] == payload["contentId"]:
                    existing = next((i for i in project["items"] if i["repository"] == repository and i["number"] == record["number"]), None)
                    item = existing or add_item(state, project, repository, record["number"])
                    return {"item": {"id": item["id"]}}
        raise NotFound()
    if name == "deleteProjectV2Item":
        project = project_by(state, ident=payload["projectId"])
        project["items"] = [i for i in project["items"] if i["id"] != payload["itemId"]]
        return {"deletedItemId": payload["itemId"]}
    if name in {"updateProjectV2ItemFieldValue", "clearProjectV2ItemFieldValue"}:
        project = project_by(state, ident=payload["projectId"])
        item = next(i for i in project["items"] if i["id"] == payload["itemId"])
        field = next(f for f in project["fields"] if f["id"] == payload["fieldId"])
        if name == "clearProjectV2ItemFieldValue":
            item["values"].pop(field["id"], None)
        else:
            value = payload["value"]
            if "singleSelectOptionId" in value:
                if value["singleSelectOptionId"] not in {o["id"] for o in field["options"]}:
                    raise SimulationError("unknown single select option")
                item["values"][field["id"]] = value["singleSelectOptionId"]
            elif "number" in value:
                item["values"][field["id"]] = value["number"]
            elif "text" in value:
                item["values"][field["id"]] = value["text"]
        return {"projectV2Item": {"id": item["id"]}}
    if name == "deleteProjectV2View":
        for project in state["projects"]:
            for view in project["views"]:
                if view["id"] == payload["viewId"]:
                    project["views"].remove(view)
                    return {"projectV2View": {"id": view["id"], "number": view["number"], "name": view["name"]}}
        raise NotFound()
    raise SimulationError(f"github_sim does not implement mutation {name}")


class Crash(Exception):
    pass


def count_mutation(state: dict[str, Any]) -> None:
    state["mutations"] += 1


def count_write_call(state: dict[str, Any]) -> None:
    """One request that changed GitHub; `crash_at` loses the response to the Nth one."""
    state["write_calls"] = state.get("write_calls", 0) + 1
    if state.get("crash_at") and state["write_calls"] == state["crash_at"]:
        state["crash_pending"] = True


# ---------------------------------------------------------------------- CLI

def main(argv: list[str]) -> int:
    path = Path(os.environ[STATE_ENV])
    state = load(path)
    if argv[:1] == ["--version"]:
        print("gh version 2.80.0 (github_sim)")
        return 0
    if argv[:2] == ["auth", "token"]:
        print("sim-token")
        return 0
    if argv[:1] != ["api"]:
        sys.stderr.write(f"github_sim: unsupported gh command {argv!r}\n")
        return 2
    args = argv[1:]
    method = "GET"
    graph = False
    endpoint = None
    reads_stdin = False
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"--hostname", "-H"}:
            index += 2
            continue
        if arg == "--method":
            method = args[index + 1]
            index += 2
            continue
        if arg == "--input":
            reads_stdin = args[index + 1] == "-"
            index += 2
            continue
        if arg == "graphql":
            graph = True
        else:
            endpoint = arg
        index += 1
    stdin = sys.stdin.read() if reads_stdin else ""
    data = json.loads(stdin) if stdin.strip() else None
    state["calls"].append({"method": method if not graph else "GRAPHQL", "endpoint": endpoint or "graphql"})
    mutations_before = state["mutations"]
    try:
        if graph:
            result: Any = {"data": graphql(state, data["query"], data.get("variables") or {})}
        else:
            state["rate"]["core"] -= 1
            result = rest(state, method, endpoint or "", data)
    except NotFound:
        save(path, state)
        sys.stderr.write("gh: Not Found (HTTP 404)\n")
        return 1
    except SimulationError as error:
        save(path, state)
        if graph:
            print(json.dumps({"data": None, "errors": [{"message": str(error)}]}))
        sys.stderr.write(f"gh: {error}\n")
        return 1
    if state["mutations"] > mutations_before:
        count_write_call(state)
    if state.pop("crash_pending", False):
        # The write happened, but the caller never hears back: a lost response.
        state["crash_at"] = None
        save(path, state)
        sys.stderr.write("github_sim: connection reset after the write was accepted\n")
        return 1
    save(path, state)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
