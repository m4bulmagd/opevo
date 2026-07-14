import packageJson from "../../package.json";

const currentYear = new Date().getFullYear();

export const APP_CONFIG = {
  name: "Presvo",
  version: packageJson.version,
  copyright: `© ${currentYear}, Presvo.`,
  capabilities: {
    realtime: process.env.NEXT_PUBLIC_REALTIME_ENABLED === "true",
  },
  meta: {
    title: "Presvo",
    description:
      "Presvo helps professional individuals and small businesses manage AI voice agents, call review, configuration, and billing.",
  },
};
