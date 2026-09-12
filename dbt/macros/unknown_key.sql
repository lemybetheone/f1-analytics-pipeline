{#
    The surrogate key of every dimension's Unknown member.

    SCHEMA §5 requires each dimension to carry a synthetic "Unknown" row, so a
    fact whose foreign key does not match still joins instead of vanishing from
    an inner join. Phase 0 measured coverage at 100% across all 26,115
    historical result rows, so this is insurance against future source drift
    rather than something load-bearing today — but a fact row that silently
    disappears is the worst kind of data loss, because the total simply comes
    out lower and nothing errors.

    Defined once, here, for a specific reason: the sentinel is written into
    every dimension *and* read back by every fact. Spelled inline in eight
    places, one of them eventually differs by a character and the fact's
    coalesce stops matching the dimension row it is meant to find — producing
    orphans that look exactly like real ones.

    `-1` rather than `0` or an empty string: it cannot collide with a real
    driverId, circuitId or status text, and it reads unmistakably as a sentinel
    when it turns up in a result set.
#}

{% macro unknown_key() -%}
    {{ dbt_utils.generate_surrogate_key(["'-1'"]) }}
{%- endmacro %}
