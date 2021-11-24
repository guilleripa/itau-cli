import logging

import click
import keyring
import requests

from client import ItauClient


@click.command()
@click.option(
    "--username", help="Itau Link username (usually identity card from Uruguay)"
)
@click.option(
    "--password",
    default=keyring.get_password("system", "pytau"),
    help="Itau Link weak password",
)
@click.option(
    "--save-csv",
    is_flag=True,
    help="Generate a CSV report for each account. (Saved in `results` folder)",
)
@click.option(
    "--csv-path",
    default="results/",
    help="Path to store csv results if enabled.",
)
@click.option("-v", "--verbose", count=True)
def main(username, password, save_csv, csv_path, verbose):
    if verbose == 0:
        log_level = None
    elif verbose == 1:
        log_level = logging.INFO
    elif verbose > 1:
        log_level = logging.DEBUG

    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("aiohttp.client").setLevel(logging.ERROR)

    if log_level:
        logging.basicConfig(
            format="%(asctime)s : %(levelname)s : %(message)s", level=log_level
        )

    client = ItauClient(username, password)
    if save_csv:
        client.save(path=csv_path)
    else:
        from IPython import embed

        embed(display_banner=False)

    # download historic dolar values
    historic_dolar = requests.get(
        "http://www.ine.gub.uy/c/document_library/get_file?uuid=1dcbe20a-153b-4caf-84a7-7a030d109471"
    )
    with open("results/historic_dolar.xlsx", "wb") as file:
        file.write(historic_dolar.content)


# %%

if __name__ == "__main__":
    main()
