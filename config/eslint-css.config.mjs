import { defineConfig } from "../.opencode/node_modules/eslint/lib/config-api.js";
import css from "../.opencode/node_modules/@eslint/css/dist/index.js";

const noDuplicateProperties = {
  meta: {
    type: "problem",
    languages: ["css/css"],
    schema: [],
    messages: {
      duplicateProperty: "Unexpected duplicate property '{{property}}'.",
    },
  },
  create(context) {
    const sourceCode = context.sourceCode;
    return {
      Block(node) {
        const properties = new Map();
        node.children.forEach((child, index) => {
          if (child.type !== "Declaration" || child.property.startsWith("--")) {
            return;
          }
          const property = child.property.toLowerCase();
          const value = `${sourceCode.getText(child.value).trim()}|${child.important === true}`;
          const previous = properties.get(property);
          if (previous) {
            const isConsecutiveFallback =
              previous.value !== value && previous.index === index - 1;
            if (!isConsecutiveFallback) {
              context.report({
                loc: child.loc,
                messageId: "duplicateProperty",
                data: { property },
              });
            }
          }
          properties.set(property, { value, index });
        });
      },
    };
  },
};

export default defineConfig([
  {
    ignores: [".venv/**", ".opencode/node_modules/**"],
  },
  {
    files: ["**/*.{css,scss,less}"],
    language: "css/css",
    languageOptions: {
      tolerant: false,
    },
    linterOptions: {
      noInlineConfig: true,
    },
    plugins: {
      css,
      "gludd-css": {
        rules: { "no-duplicate-properties": noDuplicateProperties },
      },
    },
    rules: {
      "css/no-empty-blocks": "error",
      "css/no-invalid-properties": [
        "error",
        { allowUnknownVariables: true },
      ],
      "gludd-css/no-duplicate-properties": "error",
    },
  },
]);
