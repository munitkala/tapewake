# Security

If you find a security problem, please don't open a public issue. Report it privately instead: on the repository
page go to **Security**, then **Report a vulnerability**.

tapewake runs entirely on your own computer and never uploads videos or other data. (The only thing it fetches
at the moment is the interface fonts from Google Fonts.) The things most worth
looking at are how the app starts the engine (`src-tauri/src/main.rs`) and which files the player is allowed to
open.
