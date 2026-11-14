//! Tests directs des contrôles locaux des tables FSE/Huffman de ruzstd.
//! Les modules sont exposés uniquement aux tests via `fuzz_exports`.

use ruzstd::decoding::errors::{FSETableError, HuffmanTableError};
use ruzstd::fse::FSETable;
use ruzstd::huff0::HuffmanTable;

#[test]
fn fse_rejects_symbol_and_zero_run_growth_before_modifying_the_table() {
    // Accuracy log = 5, premier symbole de probabilité zéro. Les deux bits
    // suivants demandent de sauter trois autres symboles, au-delà de max 0.
    let mut zero_run = FSETable::new(0);
    let error = zero_run.build_decoder(&[0x10, 0x06], 6).unwrap_err();
    assert!(matches!(error, FSETableError::TooManySymbols { got: 4 }));
    assert!(format!("{zero_run:?}").contains("symbol_probabilities: [0]"));

    // Le saut est nul ; le second symbole lui-même dépasserait la limite.
    let mut extra_symbol = FSETable::new(0);
    let error = extra_symbol
        .build_decoder(&[0x10, 0x00, 0, 0], 6)
        .unwrap_err();
    assert!(matches!(error, FSETableError::TooManySymbols { got: 2 }));
    assert!(format!("{extra_symbol:?}").contains("symbol_probabilities: [0]"));
}

#[test]
fn huffman_never_reads_a_truncated_fse_header_from_the_following_stream() {
    // La section de poids FSE annonce un seul octet. Les octets qui suivent
    // appartiennent au flux de littéraux et ne doivent pas compléter l'entête.
    let mut table = HuffmanTable::new();
    let error = table
        .build_decoder(&[1, 0x10, 0xff, 0xff, 0xff, 0xff])
        .unwrap_err();
    assert!(matches!(
        error,
        HuffmanTableError::FSETableError(FSETableError::GetBitsError(_))
    ));

    let mut empty = HuffmanTable::new();
    let error = empty.build_decoder(&[0, 0xff, 0xff]).unwrap_err();
    assert!(matches!(
        error,
        HuffmanTableError::FSETableError(FSETableError::GetBitsError(_))
    ));
}

#[test]
fn normal_entropy_tables_still_round_trip() {
    let input: Vec<u8> = (0..1024).map(|i| ((i * 17 + i / 7) % 251) as u8).collect();
    ruzstd::fse::round_trip(&input);
    ruzstd::huff0::round_trip(&input);
}
