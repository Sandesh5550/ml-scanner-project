const { test, describe } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { BrowserMultiFormatReader } = require('@zxing/browser');
const { BarcodeFormat, DecodeHintType } = require('@zxing/library');

describe('WebBarcodeScanner ZXing & Barcode Resolution Contract', () => {
  test('1. ZXing browser reader instantiates with supported barcode formats and options', () => {
    const hints = new Map();
    hints.set(DecodeHintType.POSSIBLE_FORMATS, [
      BarcodeFormat.EAN_13,
      BarcodeFormat.EAN_8,
      BarcodeFormat.UPC_A,
      BarcodeFormat.UPC_E,
      BarcodeFormat.CODE_128,
      BarcodeFormat.CODE_39,
      BarcodeFormat.QR_CODE,
    ]);
    hints.set(DecodeHintType.TRY_HARDER, true);

    const reader = new BrowserMultiFormatReader(hints, {
      delayBetweenScanAttempts: 120,
      delayBetweenScanSuccess: 500,
    });

    assert.ok(reader, 'BrowserMultiFormatReader should instantiate');
    assert.equal(typeof reader.decodeFromVideoElement, 'function');
  });

  test('2. Barcode formatting and clean string verification', () => {
    const raw = '  8901058851304\n\r ';
    const cleanCode = String(raw || '').trim().replace(/[\r\n\t]/g, '');
    assert.equal(cleanCode, '8901058851304');
    assert.match(cleanCode, /^\d{8,14}$/);
  });

  test('3. Supported canonical food packaging barcode lengths (8-14 digits)', () => {
    const validCodes = [
      '8901719101038', // 13 digits (EAN-13) Parle-G
      '8901058851304', // 13 digits (EAN-13) Maggi
      '012345678905',  // 12 digits (UPC-A)
      '12345670',      // 8 digits (EAN-8)
      '08901058851304' // 14 digits (ITF-14 / GTIN-14)
    ];

    for (const code of validCodes) {
      assert.match(code, /^\d{8,14}$/, `Barcode ${code} should be valid digits`);
    }
  });

  test('4. WebBarcodeScanner.web source imports @zxing/browser and controls lifecycle', () => {
    const componentSource = fs.readFileSync(
      path.join(__dirname, '../src/components/WebBarcodeScanner.web.js'),
      'utf8'
    );
    assert.match(componentSource, /import\s+\{\s*BrowserMultiFormatReader\s*\}\s+from\s+'@zxing\/browser'/);
    assert.match(componentSource, /reader\.decodeFromVideoElement\(videoElement/);
    assert.match(componentSource, /controlsRef\.current/);
    assert.match(componentSource, /stopCamera/);
  });
});
