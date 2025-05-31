{ pkgs }: {
  deps = [
    pkgs.python311
    pkgs.python311Packages.flask
    pkgs.python311Packages.pypdf2
    pkgs.python311Packages.setuptools
    pkgs.python311Packages.wheel
  ];
}
