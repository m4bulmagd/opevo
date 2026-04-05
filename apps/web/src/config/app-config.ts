import packageJson from "../../package.json";

const currentYear = new Date().getFullYear();

export const APP_CONFIG = {
  name: "AI Call Assistant",
  version: packageJson.version,
  copyright: `© ${currentYear}, AI Call Assistant.`,
  meta: {
    title: "AI Call Assistant",
    description:
      "Customer dashboard for managing agent setup, calls, configuration, and billing with the dashboard template shell.",
  },
};
