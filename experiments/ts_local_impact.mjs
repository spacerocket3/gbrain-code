import fs from "node:fs";
import path from "node:path";
import ts from "typescript";

const repo = path.resolve(process.argv[2] ?? "");
const request = JSON.parse(fs.readFileSync(0, "utf8") || "{}");

if (!repo || !Array.isArray(request.files)) {
  throw new Error("usage: node ts_local_impact.mjs REPO < request.json");
}

function compact(node, sourceFile) {
  return node.getText(sourceFile).replace(/\s+/g, " ").trim().slice(0, 320);
}

function lineOf(sourceFile, node) {
  return sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile)).line + 1;
}

function endLineOf(sourceFile, node) {
  return sourceFile.getLineAndCharacterOfPosition(node.getEnd()).line + 1;
}

function unwrap(node) {
  let current = node;
  while (current && (
    ts.isParenthesizedExpression(current)
    || ts.isAsExpression(current)
    || ts.isTypeAssertionExpression(current)
    || ts.isNonNullExpression(current)
  )) current = current.expression;
  return current;
}

function propertyPath(node, sourceFile) {
  const current = unwrap(node);
  if (!current) return "";
  if (ts.isIdentifier(current)) return current.text;
  if (current.kind === ts.SyntaxKind.ThisKeyword) return "this";
  if (ts.isPropertyAccessExpression(current)) {
    const prefix = propertyPath(current.expression, sourceFile);
    return prefix ? `${prefix}.${current.name.text}` : current.name.text;
  }
  if (ts.isElementAccessExpression(current)) {
    const prefix = propertyPath(current.expression, sourceFile);
    const argument = unwrap(current.argumentExpression);
    if (prefix && argument && ts.isStringLiteralLike(argument)) {
      return `${prefix}.${argument.text}`;
    }
  }
  return "";
}

function references(node, sourceFile) {
  const found = new Set();

  function visit(current) {
    if (!current) return;
    if (ts.isPropertyAccessExpression(current) || ts.isElementAccessExpression(current)) {
      const name = propertyPath(current, sourceFile);
      if (name) found.add(name);
      if (ts.isElementAccessExpression(current) && current.argumentExpression) {
        visit(current.argumentExpression);
      }
      return;
    }
    if (ts.isIdentifier(current)) {
      if (
        (ts.isVariableDeclaration(current.parent) && current.parent.name === current)
        || (ts.isParameter(current.parent) && current.parent.name === current)
        || (ts.isPropertyAssignment(current.parent) && current.parent.name === current)
        || (ts.isPropertyAccessExpression(current.parent) && current.parent.name === current)
      ) return;
      found.add(current.text);
      return;
    }
    if (ts.isFunctionLike(current) && current !== node) return;
    ts.forEachChild(current, visit);
  }

  visit(node);
  return [...found].sort();
}

function returnedObject(initializer) {
  const current = unwrap(initializer);
  let callback = current;
  if (ts.isCallExpression(current)) {
    const callee = propertyPath(current.expression);
    if (!callee || callee.split(".").at(-1) !== "useMemo") return null;
    callback = unwrap(current.arguments[0]);
  }
  if (!callback || (!ts.isArrowFunction(callback) && !ts.isFunctionExpression(callback))) {
    return null;
  }
  const body = unwrap(callback.body);
  if (ts.isObjectLiteralExpression(body)) return body;
  if (!ts.isBlock(body)) return null;

  let result = null;
  function findReturn(child) {
    if (result) return;
    if (child !== body && ts.isFunctionLike(child)) return;
    if (ts.isReturnStatement(child)) {
      const expression = unwrap(child.expression);
      if (expression && ts.isObjectLiteralExpression(expression)) result = expression;
      return;
    }
    ts.forEachChild(child, findReturn);
  }
  findReturn(body);
  return result;
}

function propertyName(node) {
  if (!node?.name) return "";
  if (ts.isIdentifier(node.name) || ts.isStringLiteralLike(node.name)) return node.name.text;
  return "";
}

function propertyExpression(property) {
  if (ts.isShorthandPropertyAssignment(property)) return property.name;
  if (ts.isPropertyAssignment(property)) return property.initializer;
  return null;
}

function attributeContext(element, sourceFile) {
  const fields = {};
  for (const property of element.attributes.properties) {
    if (!ts.isJsxAttribute(property)) continue;
    const name = property.name.getText(sourceFile);
    if (!property.initializer) {
      fields[name] = "true";
    } else if (ts.isStringLiteral(property.initializer)) {
      fields[name] = property.initializer.text;
    } else if (ts.isJsxExpression(property.initializer) && property.initializer.expression) {
      const expression = unwrap(property.initializer.expression);
      fields[name] = expression && ts.isStringLiteralLike(expression)
        ? expression.text
        : `{${compact(property.initializer.expression, sourceFile)}}`;
    }
  }
  return fields;
}

function analyseFile(entry) {
  const file = entry.path;
  const source = fs.readFileSync(path.join(repo, file), "utf8");
  const sourceFile = ts.createSourceFile(
    file,
    source,
    ts.ScriptTarget.Latest,
    true,
    file.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
  );
  const definitions = [];
  const sinks = [];
  let sequence = 0;

  function addDefinition(label, node, expression, spanNode = expression) {
    const definition = {
      id: `definition:${sequence++}`,
      kind: "definition",
      label,
      line: lineOf(sourceFile, node),
      end_line: endLineOf(sourceFile, node),
      span_start: lineOf(sourceFile, spanNode),
      span_end: endLineOf(sourceFile, spanNode),
      expression: compact(expression, sourceFile),
      references: references(expression, sourceFile),
    };
    definitions.push(definition);
    return definition;
  }

  function collectDefinitions(node) {
    if (
      ts.isVariableDeclaration(node)
      && ts.isIdentifier(node.name)
      && node.initializer
    ) {
      const variable = node.name.text;
      addDefinition(variable, node, node.initializer);
      const initializer = unwrap(node.initializer);
      const object = ts.isObjectLiteralExpression(initializer)
        ? initializer
        : returnedObject(initializer);
      if (object) {
        for (const property of object.properties) {
          const name = propertyName(property);
          const expression = propertyExpression(property);
          if (name && expression) addDefinition(`${variable}.${name}`, property, expression);
        }
      }
    }
    ts.forEachChild(node, collectDefinitions);
  }

  function collectSinks(node) {
    if (ts.isJsxSelfClosingElement(node) || ts.isJsxOpeningElement(node)) {
      const component = node.tagName.getText(sourceFile);
      const context = attributeContext(node, sourceFile);
      for (const property of node.attributes.properties) {
        if (!ts.isJsxAttribute(property)) continue;
        if (!property.initializer || !ts.isJsxExpression(property.initializer)) continue;
        if (!property.initializer.expression) continue;
        const name = property.name.getText(sourceFile);
        const expression = property.initializer.expression;
        sinks.push({
          id: `sink:${sequence++}`,
          kind: "jsx_attribute",
          label: `${component}.${name}`,
          line: lineOf(sourceFile, property),
          end_line: endLineOf(sourceFile, property),
          expression: compact(expression, sourceFile),
          references: references(expression, sourceFile),
          context,
        });
      }
    }
    ts.forEachChild(node, collectSinks);
  }

  collectDefinitions(sourceFile);
  collectSinks(sourceFile);

  function resolve(reference, useLine) {
    const parts = reference.split(".");
    const labels = [];
    for (let length = parts.length; length >= 1; length -= 1) {
      labels.push(parts.slice(0, length).join("."));
    }
    for (const label of labels) {
      const matches = definitions
        .filter((item) => item.label === label && item.line <= useLine)
        .sort((left, right) => right.line - left.line);
      if (matches.length) return matches[0];
    }
    return null;
  }

  const adjacency = new Map();
  function connect(sourceId, target) {
    const values = adjacency.get(sourceId) ?? [];
    if (!values.some((item) => item.id === target.id)) values.push(target);
    adjacency.set(sourceId, values);
  }
  for (const target of [...definitions, ...sinks]) {
    for (const reference of target.references) {
      const sourceDefinition = resolve(reference, target.line);
      if (sourceDefinition && sourceDefinition.id !== target.id) {
        connect(sourceDefinition.id, target);
      }
    }
  }

  const flows = [];
  for (const anchor of entry.anchors ?? []) {
    const anchorDefinitions = definitions
      .filter((item) => item.span_start <= anchor.line && item.span_end >= anchor.line)
      .sort((left, right) => {
        const leftSpan = left.span_end - left.span_start;
        const rightSpan = right.span_end - right.span_start;
        return leftSpan - rightSpan || right.line - left.line;
      });
    const start = anchorDefinitions[0];
    if (!start) continue;
    const queue = [[start]];
    const visitedDepth = new Map([[start.id, 0]]);
    while (queue.length) {
      const chain = queue.shift();
      const current = chain[chain.length - 1];
      if (current.kind === "jsx_attribute") {
        flows.push({
          anchor,
          chain: chain.map((item) => ({
            kind: item.kind,
            label: item.label,
            line: item.line,
            end_line: item.end_line,
            expression: item.expression,
          })),
          sink_context: current.context,
        });
        continue;
      }
      if (chain.length >= 10) continue;
      for (const following of adjacency.get(current.id) ?? []) {
        if (chain.some((item) => item.id === following.id)) continue;
        const depth = chain.length;
        if (visitedDepth.has(following.id) && visitedDepth.get(following.id) < depth) continue;
        visitedDepth.set(following.id, depth);
        queue.push([...chain, following]);
      }
    }
  }

  const unique = new Map();
  for (const flow of flows) {
    const key = `${flow.anchor.line}:${flow.chain.map((item) => item.label).join("->")}`;
    unique.set(key, flow);
  }
  return {
    path: file,
    analyser: "typescript-best-effort-local-forward-slice",
    limitations: [
      "Lexical binding resolution is file-local and best effort.",
      "A flow path is review evidence, not proof of changed runtime behavior.",
    ],
    flows: [...unique.values()].sort((left, right) =>
      left.chain.length - right.chain.length
      || left.chain[left.chain.length - 1].line - right.chain[right.chain.length - 1].line
    ),
  };
}

const output = { files: {} };
for (const entry of request.files) {
  if (!entry?.path || !/\.(?:ts|tsx|mts|cts)$/.test(entry.path)) continue;
  const absolute = path.join(repo, entry.path);
  if (!fs.existsSync(absolute)) continue;
  output.files[entry.path] = analyseFile(entry);
}

process.stdout.write(JSON.stringify(output));
