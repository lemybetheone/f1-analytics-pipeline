{#
    Route models to the schema they name, instead of prefixing it onto the
    target schema.

    dbt's default is to *concatenate*: a model configured with
    `+schema: marts` against a target schema of `staging` is built into
    `staging_marts`. That default exists so several developers can share one
    warehouse without overwriting each other — everyone gets their own prefix.

    It is wrong here for two reasons. The layer schemas are fixed by the
    architecture (`raw` / `staging` / `marts`, SCHEMA §1) and already exist,
    created by migration 001 and owned by `f1_pipeline`. And the pipeline role
    deliberately cannot create schemas — least privilege, migration 002 — so a
    build targeting `staging_marts` would not merely land in the wrong place,
    it would fail outright with "permission denied for database".

    Trade-off, stated because it is the reason dbt ships the other default:
    this removes per-developer schema isolation. Two people building
    simultaneously would overwrite each other's models. That is acceptable for
    a single-maintainer project and would need revisiting the moment it is not
    — the fix being to reinstate a prefix for non-production targets.
#}

{% macro generate_schema_name(custom_schema_name, node) -%}

    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}

{%- endmacro %}
