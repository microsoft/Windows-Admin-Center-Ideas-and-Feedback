Hi @{{ author }} — thanks for taking the time to file this, and apologies for the trouble.

**What we understood:** {{ summary }}

{% if missing_info -%}
To help us reproduce and prioritize, could you confirm a few details?

{% for item in missing_info -%}
- {{ item }}
{% endfor %}
{%- endif %}

{% if ado_id and ado_id > 0 -%}
We're tracking this internally as **{{ ado_link }}**. We'll post back on this issue when there's news.
{%- else -%}
We've routed this to the right area on our team and will follow up here when there's news.
{%- endif %}

— The Windows Admin Center team

<!-- triaged-by: wac-feedback-bot -->
<!-- ado-id: {{ ado_id }} -->
<!-- triage-version: 1 -->
