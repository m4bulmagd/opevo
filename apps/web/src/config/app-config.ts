import packageJson from "../../package.json";

const currentYear = new Date().getFullYear();

export const APP_CONFIG = {
  name: "Presvo",
  version: packageJson.version,
  copyright: `© ${currentYear}, Presvo.`,
  meta: {
    title: "Presvo",
    description:
      "Presvo helps professional individuals and small businesses manage AI voice agents, call review, configuration, and billing.",
  },
};
