import os
import secrets
import xml.etree.ElementTree as ET
from typing import Union

import requests as r
from rich.console import Console


def get_token() -> str:
    return secrets.token_urlsafe(4)


def xread(
    filename: Union[str, None] = None, plain_string: Union[str, None] = None
) -> Union[dict, bool]:
    """
    Reads an XML file and returns its contents as a dictionary.

    :param filename: The name of the XML file to read.
    :return: A dictionary representing the XML file's structure.
    """
    if [filename, plain_string] == [None, None]:
        raise RuntimeError("Atleast need an argument !!!")
    try:
        if plain_string:
            filename = f"./temp-{get_token()}.rss"
            with open(filename, "w") as f:
                f.write(plain_string)
        tree = ET.parse(str(filename))
        root = tree.getroot()

        try:
            os.remove(filename)
        except Exception:
            pass

        def parse_element(element):
            return {
                element.tag: {child.tag: parse_element(child) for child in element}
                or element.text
            }

        return parse_element(root)
    except Exception as e:
        print(e)
        return False


def xwrite(filename: str, data: dict) -> bool:
    """
    Writes a dictionary to an XML file.

    :param filename: The name of the XML file to write.
    :param data: A dictionary representing the XML structure.
    """
    # data = {
    #     'root': {
    #         'child1': {'subchild1': 'value1', 'subchild2': 'value2'},
    #         'child2': 'value3'
    #     }
    # }
    try:

        def dict_to_element(tag, d):
            elem = ET.Element(tag)
            for key, value in d.items():
                if isinstance(value, dict):
                    child = dict_to_element(key, value)
                    elem.append(child)
                else:
                    child = ET.Element(key)
                    child.text = str(value)
                    elem.append(child)
            return elem

        root_tag = list(data.keys())[0]
        root = dict_to_element(root_tag, data[root_tag])

        tree = ET.ElementTree(root)
        tree.write(filename, encoding="utf-8", xml_declaration=True)
        return True
    except Exception:
        return False


print = Console().print


# print(
#     xread(
#         plain_string=r.get("https://news.google.com/rss?hl=en-IN&gl=IN&ceid=IN:en").text
#     )
# )
