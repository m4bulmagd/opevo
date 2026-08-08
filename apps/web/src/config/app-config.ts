import packageJson from "../../package.json";

const currentYear = new Date().getFullYear();

export const APP_CONFIG = {
  name: "Opevo",
  version: packageJson.version,
  copyright: `© ${currentYear}, Opevo.`,
  capabilities: {
    realtime: process.env.NEXT_PUBLIC_REALTIME_ENABLED === "true",
  },
  meta: {
    title: "Opevo",
    description:
      "Opevo helps professional individuals and small businesses manage AI voice agents, call review, configuration, and billing.",
  },
};
