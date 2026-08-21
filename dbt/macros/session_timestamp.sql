{#
    Build a UTC timestamp from a session block in the races payload.

    A macro because the same six lines would otherwise be repeated for every
    session on the weekend — practice one, two and three, qualifying, sprint,
    sprint qualifying. Six copies of a null-handling cast is six chances to fix
    a bug in five places.

    Think of it as a SQL function that is expanded at compile time rather than
    executed by the database. Check `target/compiled/` to see what it becomes.

    Returns NULL unless *both* date and time are present. Sessions are only
    scheduled in the source from the mid-2000s onward, and CONVENTIONS say all
    timestamps are UTC — so defaulting a missing time to midnight would invent
    a start time that never existed, which is worse than admitting we do not
    know it.
#}

{% macro session_timestamp(payload_column, session_key) -%}
    case
        when {{ payload_column }} -> '{{ session_key }}' ->> 'date' is not null
         and {{ payload_column }} -> '{{ session_key }}' ->> 'time' is not null
        then (
                 ({{ payload_column }} -> '{{ session_key }}' ->> 'date') || ' ' ||
                 ({{ payload_column }} -> '{{ session_key }}' ->> 'time')
             )::timestamptz
    end
{%- endmacro %}
