/* eslint-disable no-undef */
const devCerts = require("office-addin-dev-certs");
const CopyWebpackPlugin = require("copy-webpack-plugin");
const HtmlWebpackPlugin = require("html-webpack-plugin");
const webpack = require("webpack");

const DEV_URL = "https://localhost:3001/";
// Set ADDIN_PUBLIC_URL when hosting the built pane somewhere other than the dev server.
const PROD_URL = process.env.ADDIN_PUBLIC_URL || DEV_URL;
// Empty means same origin; in development the dev server proxies the API below.
const API_URL = process.env.WORLDSTATE_API_URL || "";
const API_PROXY = process.env.WORLDSTATE_API_PROXY || "http://localhost:8000";

module.exports = async (env, options) => {
  const dev = options.mode === "development";
  return {
    devtool: "source-map",
    entry: {
      taskpane: "./src/taskpane/index.tsx",
      commands: "./src/commands/commands.ts",
    },
    output: { clean: true },
    resolve: { extensions: [".ts", ".tsx", ".js"] },
    module: {
      rules: [{ test: /\.tsx?$/, exclude: /node_modules/, use: "ts-loader" }],
    },
    plugins: [
      new webpack.DefinePlugin({ WORLDSTATE_API_URL: JSON.stringify(API_URL) }),
      new HtmlWebpackPlugin({ filename: "taskpane.html", template: "./src/taskpane/taskpane.html", chunks: ["taskpane"] }),
      new HtmlWebpackPlugin({ filename: "commands.html", template: "./src/commands/commands.html", chunks: ["commands"] }),
      new CopyWebpackPlugin({
        patterns: [
          { from: "assets/*", to: "assets/[name][ext][query]" },
          {
            from: "manifest.xml",
            to: "manifest.xml",
            transform: (content) => (dev ? content : content.toString().replace(new RegExp(DEV_URL, "g"), PROD_URL)),
          },
        ],
      }),
    ],
    devServer: {
      headers: { "Access-Control-Allow-Origin": "*" },
      server: {
        type: "https",
        options: env.WEBPACK_BUILD || options.https !== undefined ? options.https : await devCerts.getHttpsServerOptions(),
      },
      port: Number(process.env.npm_package_config_dev_server_port || 3001),
      proxy: [{ context: ["/v1", "/health"], target: API_PROXY, changeOrigin: true }],
    },
  };
};
