// Learn more https://docs.expo.io/guides/customizing-metro
const { getDefaultConfig } = require('expo/metro-config');

/** @type {import('expo/metro-config').MetroConfig} */
const config = getDefaultConfig(__dirname);

// Use in-process worker to prevent spawn EPERM on Windows environments
config.maxWorkers = 1;

module.exports = config;
