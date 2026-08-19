---
name: people-memory
description: >-
  Maintain a structured memory of people — names, aliases, relationships,
  attributes. Retrieve dossiers when people are mentioned in conversation.
always: true
triggers: person mentioned by name, user talks about family friends or colleagues, user introduces someone new
inject_on_trigger: true
---

# People memory

Maintain a structured, long-term memory of the people the user knows. Facts are
stored as semantic-memory keys under the `people.*` prefix and are retrieved as
a dossier whenever a person is mentioned.

## The key schema

Every fact is a `people.<name>.<attribute>` key:

- `people.alice.name` — the person's canonical name
- `people.alice.birthday` — a date or age
- `people.alice.relationship` — how they relate to the user
- `people.alice.aliases` — alternate names, e.g. "Ali"
- `people.alice.attributes` — a JSON object of free-form attributes

Names are normalized to lowercase alphanumeric (underscores allowed): "Alice
Smith" becomes `alice_smith`. Use the same normalized name for every fact about
that person.

## Storing a fact

Use the `people_add_fact` tool with `name`, `attribute`, and `value`:

- `people_add_fact(name="alice", attribute="birthday", value="1990-01-01")`
- `people_add_fact(name="alice", attribute="relationship", value="coworker")`

## Relationships

Store relationships as a JSON object under the `relationships` attribute:

- `people_add_fact(name="alice", attribute="relationships", value={"bob": "coworker"})`

The value maps another person's normalized name to the kind of relationship. To
add a relationship, read the existing `relationships` value, merge the new edge,
and write the whole object back.

## Retrieving a dossier

When a person is mentioned by name, call `people_lookup(name=...)` to fetch all
`people.<name>.*` facts and use them as context. Use `people_list` to see who is
known.

## Resolving aliases

If `people.alice.aliases` is "Ali", treat "Ali" as referring to `alice`. When a
name you look up returns nothing, check whether it is an alias of a known person
and look up the canonical name instead.

## Rules

- Never store passwords, credentials, SSNs, or other secrets.
- Ask the user before remembering a new person.
- Keep facts factual and current; update a fact when the user corrects it.
