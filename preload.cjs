'use strict';
const {contextBridge} = require('electron');
const version = process.argv.find(value=>value.startsWith('--reborn-client-version='))?.split('=')[1] || '';
contextBridge.exposeInMainWorld('rebornDesktop', Object.freeze({version}));
// Also correct the label when connecting to an older backend web interface.
window.addEventListener('DOMContentLoaded', () => {
  const label = document.querySelector('.version');
  if(label && version) label.textContent='REBORN / v'+version;
});
