"""Simple statistics about a text."""


def words(text):
    """The words of text, lowercased, without the punctuation around them."""
    return text.split()


def word_count(text):
    return len(words(text))


def most_common(text, n):
    """The n most common words with their counts, most common first; ties in
    alphabetical order."""
    raise NotImplementedError


def sentences(text):
    """The number of sentences: runs of text ending in '.', '!' or '?'."""
    raise NotImplementedError
