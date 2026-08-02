#!/usr/bin/env python3
"""framework_integration — React, Next.js, HTMX, GraphQL, REST API tooling."""
import argparse
import json
import os
import re

REACT_COMPONENT_TEMPLATE = '''\
import React, {{ useState, useEffect }} from 'react';

interface {name}Props {{
  title?: string;
}}

export default function {name}({{ title = '{name}' }}: {name}Props) {{
  const [data, setData] = useState<string | null>(null);

  useEffect(() => {{
    const controller = new AbortController();
    fetch('/api/data', {{ signal: controller.signal }})
      .then(res => res.json())
      .then(setData)
      .catch(err => {{
        if (err.name !== 'AbortError') console.error(err);
      }});
    return () => controller.abort();
  }}, []);

  return (
    <section className="{name_lower}">
      <h2>{{title}}</h2>
      {{data ? <pre>{{JSON.stringify(data, null, 2)}}</pre> : <p>Loading...</p>}}
    </section>
  );
}}
'''

NEXTJS_PAGE_TEMPLATE = '''\
import {{ Metadata }} from 'next';
import {{ Suspense }} from 'react';

export const metadata: Metadata = {{
  title: '{title_case}',
  description: '{description}',
}};

export const dynamic = 'force-dynamic';

async function fetchData() {{
  const res = await fetch('{endpoint}', {{ next: {{ revalidate: 60 }} }});
  if (!res.ok) throw new Error('Failed to fetch');
  return res.json();
}}

function Content({{ data }}: {{ data: any }}) {{
  return <pre>{{JSON.stringify(data, null, 2)}}</pre>;
}}

function Loading() {{
  return <p>Loading {title_case}...</p>;
}}

export default async function {title_case}Page() {{
  const data = await fetchData();
  return (
    <main>
      <h1>{title_case}</h1>
      <Suspense fallback={<Loading />}>
        <Content data={{data}} />
      </Suspense>
    </main>
  );
}}
'''

NEXTJS_ROUTE_TEMPLATE = '''\
import {{ NextRequest, NextResponse }} from 'next/server';

export async function GET(request: NextRequest) {{
  const {{ searchParams }} = new URL(request.url);
  const page = searchParams.get('page') || '1';
  const limit = searchParams.get('limit') || '10';

  const payload = {{
    page: parseInt(page, 10),
    limit: parseInt(limit, 10),
    data: [],
    meta: {{ total: 0, page: parseInt(page, 10), limit: parseInt(limit, 10) }},
  }};

  return NextResponse.json(payload, {{
    status: 200,
    headers: {{
      'Cache-Control': 'public, max-age=30, stale-while-revalidate=60',
    }},
  }});
}}
'''

HTMX_TEMPLATE = '''\
<form hx-post="/api/{name_kebab}"
      hx-target="#{name_kebab}-result"
      hx-swap="innerHTML"
      hx-indicator="#{name_kebab}-spinner"
      hx-validate="true">
  <fieldset>
    <label for="{name_kebab}-input">Value:</label>
    <input id="{name_kebab}-input" name="value" type="text" required />
  </fieldset>
  <button type="submit">
    Submit
    <span id="{name_kebab}-spinner" class="htmx-indicator" aria-hidden="true">...</span>
  </button>
</form>
<div id="{name_kebab}-result" role="status" aria-live="polite"></div>

<style>
  .htmx-indicator {{ display: none; }}
  .htmx-request .htmx-indicator {{ display: inline; }}
  .htmx-request#{{ '{name_kebab}-spinner'.format(name_kebab=name_kebab) }} {{ display: inline; }}
</style>
'''

GRAPHQL_QUERY_TEMPLATE = '''\
query {name_title}($first: Int! = 10, $after: String) {{
  {name_camel}(first: $first, after: $after) {{
    edges {{
      node {{
        id
        ...{name_title}Fragment
      }}
    }}
    pageInfo {{
      hasNextPage
      endCursor
    }}
  }}
}}

fragment {name_title}Fragment on {name_title} {{
  id
  createdAt
  updatedAt
}}
'''

GRAPHQL_MUTATION_TEMPLATE = '''\
mutation {name_title}Create($input: {name_title}CreateInput!) {{
  {name_title}Create(input: $input) {{
    {name_camel} {{
      id
      ...{name_title}Fragment
    }}
    errors {{
      field
      message
    }}
  }}
}}

fragment {name_title}Fragment on {name_title} {{
  id
  createdAt
  updatedAt
}}
'''

REST_TEST_TEMPLATE = '''\
import fetch from 'node-fetch';

async function testEndpoint() {{
  const url = '{endpoint}';
  try {{
    const res = await fetch(url, {{
      method: 'GET',
      headers: {{ 'Accept': 'application/json' }},
    }});

    console.log('Status:', res.status, res.statusText);
    console.log('Headers:', Object.fromEntries(res.headers.entries()));

    if (res.ok) {{
      const data = await res.json();
      console.log('Response:', JSON.stringify(data, null, 2));
    }} else {{
      const error = await res.text();
      console.error('Error body:', error.slice(0, 500));
    }}
  }} catch (err) {{
    console.error('Fetch error:', err.message);
  }}
}}

testEndpoint();
'''


def _inflect(name: str) -> dict[str, str]:
    title_case = name[0].upper() + name[1:] if name else ""
    return {
        "name": name,
        "name_title": title_case,
        "name_lower": name.lower(),
        "name_kebab": re.sub(r'(?<!^)(?=[A-Z])', '-', name).lower(),
        "name_camel": name[0].lower() + name[1:] if name else "",
        "title": title_case,
        "description": title_case + " page",
        "endpoint": "/api/" + re.sub(r'(?<!^)(?=[A-Z])', '-', name).lower(),
    }


def scaffold_react(component_name: str) -> str:
    parts = _inflect(component_name)
    return REACT_COMPONENT_TEMPLATE.format(**parts)


def generate_nextjs_page(component_name: str, endpoint_url: str) -> str:
    parts = _inflect(component_name)
    parts["endpoint"] = endpoint_url or "/api/data"
    parts["description"] = parts["title"] + " page"
    return NEXTJS_PAGE_TEMPLATE.format(**parts)


def generate_nextjs_route(component_name: str) -> str:
    return NEXTJS_ROUTE_TEMPLATE.format()


def scaffold_htmx(component_name: str) -> str:
    return HTMX_TEMPLATE.format(**_inflect(component_name))


def generate_graphql_query(component_name: str) -> str:
    return GRAPHQL_QUERY_TEMPLATE.format(**_inflect(component_name))


def generate_graphql_mutation(component_name: str) -> str:
    return GRAPHQL_MUTATION_TEMPLATE.format(**_inflect(component_name))


def test_rest_endpoint(endpoint_url: str) -> str:
    return REST_TEST_TEMPLATE.format(endpoint=endpoint_url, **_inflect("TestEndpoint"))


def parse_graphql_schema(schema_file: str) -> dict:
    if not os.path.isfile(schema_file):
        return {"parsed": False, "error": f"Schema file not found: {schema_file}"}
    with open(schema_file) as f:
        content = f.read()
    types_found = []
    for match in re.finditer(r'type\s+(\w+)\s*\{', content):
        types_found.append(match.group(1))
    queries = re.findall(r'(?:query|mutation)\s+(\w+)', content)
    return {
        "parsed": True,
        "type_count": len(types_found),
        "types": types_found,
        "query_count": len(queries),
        "queries": queries,
        "error": None,
    }


def run_test_endpoint(endpoint_url: str) -> dict:
    import urllib.error
    import urllib.request
    try:
        req = urllib.request.Request(endpoint_url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8")
        return {
            "endpoint": endpoint_url,
            "status": resp.status,
            "content_type": resp.headers.get("Content-Type", ""),
            "body_preview": body[:500],
            "error": None,
        }
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")[:500] if e.fp else ""
        return {"endpoint": endpoint_url, "status": e.code, "error_body": body, "error": str(e)}
    except Exception as e:
        return {"endpoint": endpoint_url, "status": 0, "error": str(e)}


def analyze_htmx(template_content: str) -> dict:
    attrs = re.findall(r'hx-(\w+)', template_content)
    required = {"get", "post"}
    found = set(attrs)
    return {
        "valid": True,
        "htmx_attributes": list(found),
        "missing_http_method": bool(found.isdisjoint(required)),
        "count": len(attrs),
    }


def main():
    parser = argparse.ArgumentParser(description="framework_integration operations")
    parser.add_argument("--framework", required=True, choices=["react", "nextjs", "htmx", "graphql", "rest"])
    parser.add_argument("--operation", required=True, choices=["scaffold", "generate", "test", "analyze"])
    parser.add_argument("--component-name", default="Component")
    parser.add_argument("--endpoint-url", default="")
    parser.add_argument("--graphql-schema-file", default="")
    parser.add_argument("--output-dir", default="/tmp/gludd-web/framework_integration")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    result: dict = {"framework": args.framework, "operation": args.operation}

    if args.framework == "react" and args.operation == "scaffold":
        code = scaffold_react(args.component_name)
        ext = ".tsx"
    elif args.framework == "nextjs" and args.operation == "scaffold":
        code = generate_nextjs_page(args.component_name, args.endpoint_url)
        ext = ".tsx"
    elif args.framework == "nextjs" and args.operation == "generate":
        code = generate_nextjs_route(args.component_name)
        ext = ".ts"
    elif args.framework == "htmx" and args.operation == "scaffold":
        code = scaffold_htmx(args.component_name)
        ext = ".html"
    elif args.framework == "htmx" and args.operation == "analyze":
        if args.component_name and os.path.isfile(args.component_name):
            with open(args.component_name) as f:
                content = f.read()
        else:
            content = scaffold_htmx(args.component_name)
        result.update(analyze_htmx(content))
        ext = ".json"
        code = json.dumps(result, indent=2)
    elif args.framework == "graphql" and args.operation == "generate":
        code = generate_graphql_query(args.component_name)
        ext = ".graphql"
    elif args.framework == "graphql" and args.operation == "analyze":
        result.update(parse_graphql_schema(args.graphql_schema_file))
        ext = ".json"
        code = json.dumps(result, indent=2)
    elif args.framework == "rest" and args.operation == "test":
        result.update(run_test_endpoint(args.endpoint_url))
        ext = ".json"
        code = json.dumps(result, indent=2)
    elif args.framework == "rest" and args.operation == "generate":
        code = test_rest_endpoint(args.endpoint_url)
        ext = ".ts"
    else:
        result["error"] = f"Unsupported: framework={args.framework} operation={args.operation}"
        code = json.dumps(result, indent=2)
        ext = ".json"

    if ext == ".json":
        output_path = os.path.join(args.output_dir, "framework_integration" + ext)
    else:
        output_path = os.path.join(args.output_dir, args.component_name.lower() + ext)

    with open(output_path, "w") as f:
        f.write(code)

    result["output_file"] = output_path
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
