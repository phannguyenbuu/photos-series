// Ví dụ cấu hình expose PhotoSeries qua Webpack 5 ModuleFederationPlugin
const path = require('node:path');
const { ModuleFederationPlugin } = require('webpack').container;
const MiniCssExtractPlugin = require('mini-css-extract-plugin');

module.exports = {
  mode: 'production',
  output: {
    uniqueName: 'remote_photo_series',
    publicPath: 'auto',
    clean: true,
  },
  plugins: [
    new ModuleFederationPlugin({
      name: 'remote_photo_series',
      filename: 'remoteEntry.js',
      exposes: {
        './PhotoSeries': path.resolve(__dirname, '../photo_series/PhotoSeriesSection.tsx'),
        './PhotoSeriesSection': path.resolve(__dirname, '../photo_series/PhotoSeriesSection.tsx'),
      },
      shared: {
        react: { singleton: true },
        'react-dom': { singleton: true },
      },
    }),
    new MiniCssExtractPlugin(),
  ],
};
