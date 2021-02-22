# Changelog for mokapot  

## [Unreleased]  
### Added  
- Support for parsing PSMs from PepXML input files.
- A changelog

### Fixed  
- When the learned model was worse than the best feature and the lower scores
  were better for the best feature, assigning confidence would fail.
- Easy access to grouped confidence estimates in the Python API were not working
  due to a typo.
- Deprecation warnings from Pandas about the `regex` argument.

### Changed  
- Refactored and added many new unit and system tests.