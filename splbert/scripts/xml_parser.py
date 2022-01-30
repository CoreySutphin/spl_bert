"""
    Tools to parse out information from drug labels represented as XML documents.
    Used to process the annotated TAC Structured Product Labels(SPL) 2019 dataset.

    To Use:
        python xml_parser.py xml-directory-path brat-directory-path --json --brat
"""

import sys
import os
import spacy
import json
import warnings
import xml.etree.ElementTree as ET
from spacy.gold import biluo_tags_from_offsets, docs_to_json


nlp = spacy.load("en_core_web_sm")


def handle_parsing_errors(mentions):
    """Takes in a directory of mentions, and applies heuristics to clean up problematic mentions.

    Returns a list of mention IDs to remove.
    """
    mentions_to_remove = []
    for mention in mentions:
        mention_start = mention["start"]
        mention_end = mention["end"]

        # Mentions that contain " and " are two entities that have been concatenated together
        if " and " in mention["str"]:
            mentions_to_remove.append(mention["id"])
            continue

        # A lot of files have a weird case where a subword is annotated instead of the full word
        # The section below filters out those cases.
        if mention["str"] == "dihydroergotamine":
            other_mention = [m for m in mentions if m["str"] == "ergotamine"]
            if other_mention:
                mentions_to_remove.append(other_mention[0]["id"])
        if mention["str"] == "desvenlafaxine":
            other_mention = [m for m in mentions if m["str"] == "venlafaxine"]
            if other_mention:
                mentions_to_remove.append(other_mention[0]["id"])
        if mention["str"] == "temsirolimus":
            other_mention = [m for m in mentions if m["str"] == "sirolimus"]
            if other_mention:
                mentions_to_remove.append(other_mention[0]["id"])

        # Some mentions will have overlapping spans, which is problematic for tokenization.
        # The section below handles those mentions.
        for other in mentions:
            if mention["start"] == other["start"] and mention["id"] != other["id"]:
                # If two mentions have the same start span, and one contains a '/', remove the one that contains "/".
                if "/" in other["str"]:
                    mentions_to_remove.append(other["id"])
                # If two mentions have the same start span, and the strings only differ by an 's' at the
                # end, remove the singular form.
                if mention["str"].endswith("s") and mention["str"][:-1] == other["str"]:
                    mentions_to_remove.append(other["id"])
            # If a mention is encapsulated in another mention from the same sentence,
            # then we have boundary overlap.
            # TODO: This could be problematic when parsing in non-continuous entities
            if mention["str"] in other["str"] and mention["id"] != other["id"]:
                mentions_to_remove.append(other["id"])

    return mentions_to_remove


def parse_xml(file):
    """Convert an XML file representing annotated drug labels to brat annotation format.

    Args:
        file (string) - Path to the XML file to be converted
    """
    tree = ET.parse(file)
    root = tree.getroot()
    drug_name = root.get("drug")

    sentences = []
    mentions = []

    for child in root:
        if child.tag == "Sentences":
            total_offset = 0
            for sentence in child.findall("Sentence"):
                sentence_text = sentence.find("SentenceText").text.strip()
                sentence_id = sentence.get("id")
                sentences.append({"text": sentence_text, "id": sentence_id})

                sentence_mentions = []
                for mention in sentence.findall("Mention"):
                    attrib = mention.attrib

                    # IMPORTANT: Currently skipping all mentions with non-contiguous entities, indicated by a ';' in the span
                    if ";" in attrib["span"]:
                        continue

                    span = attrib["span"].strip()
                    # Calculate the start/end offsets of this mention in the sentence
                    start, length = [int(x) for x in span.split(" ")]
                    end = start + length

                    # Calculate the start/end offsets of this mention in the document
                    document_start = start + total_offset
                    document_end = document_start + length

                    sentence_mentions.append(
                        {
                            "id": attrib["id"].strip(),
                            "type": attrib["type"].strip(),
                            "start": start,
                            "end": end,
                            "document_start": document_start,
                            "document_end": document_end,
                            "str": attrib["str"].strip().lower(),
                        }
                    )

                # Look for any overlapping mentions in the sentence
                mentions_to_remove = handle_parsing_errors(sentence_mentions)

                if mentions_to_remove:
                    sentence_mentions = [
                        mention
                        for mention in sentence_mentions
                        if mention["id"] not in mentions_to_remove
                    ]

                mentions.append(sentence_mentions)
                total_offset += len(sentence_text)

    document = {
        "file_name": os.path.basename(file),
        "mentions": mentions,
        "sentences": sentences,
        "drug_name": drug_name,
    }
    if not document["sentences"] or not document["mentions"]:
        print(
            f"File {document['file_name']} did not have any mentions and could not be converted."
        )
    return document


# TODO - Fix this, broken since changing document["text"] to be a list of strings rather than a single string
def convert_to_brat(documents, output_directory=""):
    for document in documents:
        # Construct a new .txt and .ann file for this document
        document_filename = document["file_name"]
        text_filename = os.path.join(
            output_directory, document_filename.replace(".xml", ".txt")
        )
        ann_filename = os.path.join(
            output_directory, document_filename.replace(".xml", ".ann")
        )

        with open(text_filename, "w+") as f:
            f.write(document["sentences"])

        with open(ann_filename, "w+") as f:
            for (index, mention) in enumerate(document["mentions"]):
                f.write(
                    f"T{index + 1}\t{mention['type']} {mention['document_start']} {mention['document_end']}\t{mention['str']}\n"
                )


def convert_to_spacy_encodings(
    documents, output_directory="", mention_type="Precipitant"
):
    incorrect_mentions = 0
    counter = 1
    for document in documents:
        print(f"Converting file {counter}: {document['file_name']}")
        file_name = os.path.join(
            output_directory, document["file_name"].replace(".xml", ".json")
        )

        sentences = document["sentences"]
        mentions = document["mentions"]

        # Encoded sequences that will be saved later
        token_sequences = []
        label_sequences = []
        text_sequences = []

        for i in range(len(mentions)):
            sentence = sentences[i]
            mention_sequence = mentions[i]

            entities = [
                (mention["start"], mention["end"], mention["type"])
                for mention in mention_sequence
                if mention["type"] == mention_type
            ]

            doc = nlp(sentence["text"])

            try:
                labels = biluo_tags_from_offsets(doc, entities)
                token_sequences.append([str(token) for token in doc])
                label_sequences.append(labels)
                text_sequences.append(sentence)
            except ValueError as e:
                print(f"Error in document: {document['file_name']}")
                incorrect_mentions += 1
                raise e
            # except Warning:
            #     print([token for token in doc])
            #     print(doc.text[82:85])
            #     print(doc.char_span(82, 86))
            #     print(entities)

        # Write the SpaCy doc and associated tags to the output directory
        output = {
            "tokens": token_sequences,
            "labels": label_sequences,
            "drug_name": document["drug_name"],
            "file_name": document["file_name"],
            "sentences": sentences,
        }

        with open(file_name, "w+") as f:
            f.write(json.dumps(output))

        counter += 1


opts = [opt for opt in sys.argv[1:] if opt.startswith("-")]
args = [arg for arg in sys.argv[1:] if not arg.startswith("-")]

directory_name = args[0]
output_directory = args[1]

documents = []
for filename in os.listdir(directory_name):
    document = parse_xml(os.path.join(directory_name, filename))
    documents.append(document)

if "--json" in opts:
    convert_to_spacy_encodings(documents, output_directory)
elif "--brat" in opts:
    convert_to_brat(documents, output_directory)
