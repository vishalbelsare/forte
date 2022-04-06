#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from typing import Dict, List, Iterator, Tuple, Optional, Any
import uuid
from bisect import bisect_left
from heapq import heappush, heappop
from sortedcontainers import SortedList

from forte.utils import get_class
from forte.data.base_store import BaseStore
from forte.data.ontology.top import Annotation, AudioAnnotation
from forte.common import constants

__all__ = ["DataStore"]


class DataStore(BaseStore):
    # TODO: temporarily disable this for development purposes.
    # pylint: disable=pointless-string-statement

    def __init__(
        self, onto_file_path: Optional[str] = None, dynamically_add_type=True
    ):
        r"""An implementation of the data store object that mainly uses
        primitive types. This class will be used as the internal data
        representation behind data pack. The usage of primitive types provides
        a speed-up to the previous class-based solution.

        A DataStore object uses primitive types and simple python data
        structures to store a collection of Forte entries for certain types of
        unstructured data.
        Currently, DataStore supports storing data structures with linear span
        (e.g. Annotation), and relational data structures (e.g Link and Group).
        Future extension of the class may support data structures with 2-d range
         (e.g. bounding boxes).

        Internally, we store every entry in a variable ``__elements``, which is
        a nested list: a list of ``entry lists``.

        Every inner list, the ``entry list``, is a list storing entries for a
        single particular type, such as entries for
        :class:`~ft.onto.base_ontology.Sentence`. Different types are stored in
        different lists: [ <Document List>, <Sentence List>, ...]. We will
        discuss the sorting order later.

        The outer list, stores a list of ``entry lists``,
        and each ``entry list``
        is indexed by the type of its element. Specifically, each type is
        associated with a unique ``type_id``, which is generated by the system.
        The mapping between ``type_name`` and ``type_id`` is defined by a
        dictionary ``self.__type_index_dict``.

        Entry information is stored as ``entry data`` in each ``entry list``.
        Each element in the ``entry list`` (an entry data) corresponds to one
        entry instance.

        Each ``entry data`` in the ``entry list`` is represented by a list of
        attributes.
        For example, an annotation type entry has the following format:
        [<begin>, <end>, <tid>, <type_name>, <attr_1>, <attr_2>, ...,
        <attr_n>].
        A group type entry has the following format:
        [<member_type>, <[members_tid_list]>, <tid>, <type_name>, <attr_1>,
            <attr_2>, ..., <attr_n>, index_id].
        A link type entry has the following format:
        [<parent_tid>, <child_tid>, <tid>, <type_name>, <attr_1>, <attr_2>,
        ..., <attr_n>, index_id].

        The first four fields are compulsory for every ``entry data``. The third
        and fourth fields are always ``tid`` and ``type_name``, but the first and
        second fields can change across different types of entries.
        For example, first four fields of Annotation-Like (e.g. subclasses of
        Annotation or AudioAnnotation) entries are always in the order of
        ``begin``, ``end``, ``tid`` and ``type_name``. ``begin`` and ``end``, which are
        compulsory for annotations entries, represent the begin and end
        character indices of entries in the payload.

        The last field is always ``index_id`` for entries that are not
        Annotation-like. It is an extra field to record the location of the
        entry in the list. When the user add a new entry to the data store,
        the ``index_id`` will be created and appended to the end of the original
        ``entry data`` list.

        Here, ``type_name`` is the fully qualifie name of this type represented
        by ``entry list``. It must be a valid ontology defined as a class.
        ``tid`` is a unique id of every entry, which is internally generated by
        uuid.uuid4().
        Each ``type_name`` corresponds to a pre-defined ordered list of
        attributes, the exact order is determined by the system through the
        ontology specifications.
        E.g. an annotation-type ``entry data`` with type
        :class:`~ft.onto.base_ontology.Document` has the following structure:
        [<begin>, <end>, <tid>, <type_name>, <document_class>, <sentiment>,
        <classifications>].
        Here, <document_class>, <sentiment>, <classifications> are the 3
        attributes of this type. This allows the ``entry list`` behaves like a
        table, we can find the value of an attribute through the correct
        ``index_id`` id (e.g. index of the outer list) and `attr_id`
        (e.g. index of the inner list).

        Note that, if the type of ``entry list`` is Annotation-Like (e.g.
        subclasses of Annotation or AudioAnnotation), these entries will be
        sorted by the first two attributes (``begin``, ``end``). However, the
        order of a list with types that are not Annotation-like, is currently
        based on the insertion order.

        ``onto_file_path`` is an optional argument, which allows one to pass in
        a user defined ontology file. This will enable the DataStore to
        understand and store ``entry_type`` defined in the provided file.

        Args:
            onto_file_path (str, optional): the path to the ontology file.
        """
        super().__init__()

        if onto_file_path is None and not dynamically_add_type:
            raise RuntimeError(
                "DataStore is initialized with no existing types. Setting"
                "dynamically_add_type to False without providing onto_file_path"
                "will lead to no usable type in DataStore."
            )
        self._onto_file_path = onto_file_path
        self._dynamically_add_type = dynamically_add_type

        """
        The ``_type_attributes`` is a private dictionary that provides
        ``type_name``, their parent entry, and the order of corresponding attributes.
        The keys are fully qualified names of every type; The value is a dictionary with
        two keys. Key ``attribute`` provides an inner dictionary with all valid attributes
        for this type and the indices of attributes among these lists. Key ``parent_entry``
        is a string representing the direct parent of this type.

        This structure is supposed to be built dynamically. When a user adds new entries,
        data_store will check unknown types and add them to ``_type_attributes``.

        Example:

        .. code-block:: python

            # self._type_attributes is:
            # {
            #     "ft.onto.base_ontology.Token": {
            #       "attributes": {"pos": 4, "ud_xpos": 5,
            #               "lemma": 6, "chunk": 7, "ner": 8, "sense": 9,
            #               "is_root": 10, "ud_features": 11, "ud_misc": 12},
            #       "parent_entry": "forte.data.ontology.top.Annotation", },
            #     "ft.onto.base_ontology.Document": {
            #       "attributes": {"document_class": 4,
            #               "sentiment": 5, "classifications": 6},
            #       "parent_entry": "forte.data.ontology.top.Annotation", },
            #     "ft.onto.base_ontology.Sentence": {
            #       "attributes": {"speaker": 4,
            #               "part_id": 5, "sentiment": 6,
            #               "classification": 7, "classifications": 8},
            #       "parent_entry": "forte.data.ontology.top.Annotation", }
            # }
        """
        self._type_attributes: dict = {}
        if self._onto_file_path:
            self._parse_onto_file()

        """
        The `__elements` is an underlying storage structure for all the entry
        data added by users in this DataStore class.
        It is a dict of {str: list} pairs that stores sorted ``entry lists`` by
         ``type_name``s.

            Example:
            self.__elements = {
                "ft.onto.base_ontology.Token": Token SortedList(),
                "ft.onto.base_ontology.Document": Document SortedList(),
                "ft.onto.base_ontology.Sentence": Sentence SortedList(),
                ...
            }
        """
        self.__elements: dict = {}

        """
        A dictionary that keeps record of all entries with their tid.
        It is a key-value map of {tid: entry data in list format}.

        e.g., {1423543453: [begin, end, tid, type_name, attr_1, ..., attr_n],
        4345314235: [parent_tid, child_tid, tid, type_name, attr_1, ...,
                    attr_n, index_id]}
        """
        self.__entry_dict: dict = {}

    def _new_tid(self) -> int:
        r"""This function generates a new ``tid`` for an entry."""
        return uuid.uuid4().int

    def _get_type_info(self, type_name: str) -> Dict[str, Any]:
        """
        Get the dictionary containing type information from ``self._type_attributes``.
        If the ``type_name`` does not currecntly exists and dynamic import is enabled,
        this function will add a new key-value pair into ``self._type_attributes``. The
        value consists of a full attribute-to-index dictionary and an empty parent set.

        This function returns a dictionary containing an attribute dict and a set of parent
        entries of the given type. For example:

        .. code-block:: python

            "ft.onto.base_ontology.Sentence": {
                    "attributes": {
                        "speaker": 4,
                        "part_id": 5,
                        "sentiment": 6,
                        "classification": 7,
                        "classifications": 8,
                    },
                    "parent_entry": set(),
                }

        Args:
            type_name (str): The fully qualified type name of a type.
        Returns:
            attr_dict (dict): The dictionary containing an attribute dict and a set of parent
            entries of the given type.
        Raises:
            RuntimeError: When the type is not provided by ontology file and
            dynamic import is disabled.
        """
        # check if type is in dictionary
        if type_name in self._type_attributes:
            return self._type_attributes[type_name]
        if not self._dynamically_add_type:
            raise ValueError(
                f"{type_name} is not an existing type in current data store."
                f"Dynamically add type is disabled."
                f"Set dynamically_add_type=True if you need to use types other than"
                f"types specified in the ontology file."
            )
        # get attribute dictionary
        attributes = self._get_entry_attributes_by_class(type_name)

        attr_dict = {}
        attr_idx = constants.ENTRY_TYPE_INDEX + 1
        for attr_name in attributes:
            attr_dict[attr_name] = attr_idx
            attr_idx += 1

        new_entry_info = {
            "attributes": attr_dict,
            "parent_entry": set(),
        }
        self._type_attributes[type_name] = new_entry_info

        return new_entry_info

    def _get_type_attribute_dict(self, type_name: str) -> Dict[str, int]:
        """Get the attribute dict of an entry type. The attribute dict maps
        attribute names to a list of consecutive integers as indicies. For example:
        .. code-block:: python

            "attributes": {
                        "speaker": 4,
                        "part_id": 5,
                        "sentiment": 6,
                        "classification": 7,
                        "classifications": 8,
            },

        Args:
            type_name (str): The fully qualified type name of a type.
        Returns:
            attr_dict (dict): The attribute-to-index dictionary of an entry.
        """
        return self._get_type_info(type_name)["attributes"]

    def _get_type_parent(self, type_name: str) -> str:
        """Get a set of parent names of an entry type. The set is a subset of all
        ancestors of the given type.
        Args:
            type_name (str): The fully qualified type name of a type.
        Returns:
            parent_entry (str): The parent entry name of an entry.
        """
        return self._get_type_info(type_name)["parent_entry"]

    def _num_attributes_for_type(self, type_name: str) -> int:
        """Get the length of the attribute dict of an entry type.
        Args:
            type_name (str): The fully qualified type name of the new entry.
        Returns:
            attr_dict (dict): The attributes-to-index dict of an entry.
        """
        return len(self._get_type_attribute_dict(type_name))

    def _new_annotation(self, type_name: str, begin: int, end: int) -> List:
        r"""This function generates a new annotation with default fields.
        All default fields are filled with None.
        Called by add_annotation_raw() to create a new annotation with
        ``type_name``, ``begin``, and ``end``.

        Args:
            type_name (str): The fully qualified type name of the new entry.
            begin (int): Begin index of the entry.
            end (int): End index of the entry.

        Returns:
            A list representing a new annotation type entry data.
        """

        tid: int = self._new_tid()
        entry: List[Any]

        entry = [begin, end, tid, type_name]
        entry += self._num_attributes_for_type(type_name) * [None]

        return entry

    def _new_link(
        self, type_name: str, parent_tid: int, child_tid: int
    ) -> List:
        r"""This function generates a new link with default fields. All
        default fields are filled with None.
        Called by add_link_raw() to create a new link with ``type_name``,
        ``parent_tid``, and ``child_tid``.

        Args:
            type_name (str): The fully qualified type name of the new entry.
            parent_tid (int): ``tid`` of the parent entry.
            child_tid (int): ``tid`` of the child entry.

        Returns:
            A list representing a new link type entry data.
        """

        tid: int = self._new_tid()
        entry: List[Any]

        entry = [parent_tid, child_tid, tid, type_name]
        entry += self._num_attributes_for_type(type_name) * [None]

        return entry

    def _new_group(self, type_name: str, member_type: str) -> List:
        r"""This function generates a new group with default fields. All
        default fields are filled with None.
        Called by add_group_raw() to create a new group with
        ``type_name`` and ``member_type``.

        Args:
            type_name (str): The fully qualified type name of the new entry.
            member_type (str): Fully qualified name of its members.

        Returns:
            A list representing a new group type entry data.
        """

        tid: int = self._new_tid()

        entry = [member_type, [], tid, type_name]
        entry += self._num_attributes_for_type(type_name) * [None]

        return entry

    def _is_annotation(self, type_name: str) -> bool:
        r"""This function takes a type_id and returns whether a type
        is an annotation type or not.
        Args:
            type_name (str): The name of type in `self.__elements`.
        Returns:
            A boolean value whether this type_id belongs to an annotation
            type or not.
        """
        # TODO: use is_subclass() in DataStore to replace this
        entry_class = get_class(type_name)
        return issubclass(entry_class, (Annotation, AudioAnnotation))

    def add_annotation_raw(self, type_name: str, begin: int, end: int) -> int:
        r"""This function adds an annotation entry with ``begin`` and ``end``
        indices to current data store object. Returns the ``tid`` for the inserted
        entry.

        Args:
            type_name (str): The fully qualified type name of the new Annotation.
            begin (int): Begin index of the entry.
            end (int): End index of the entry.

        Returns:
            ``tid`` of the entry.
        """
        # We should create the `entry data` with the format
        # [begin, end, tid, type_id, None, ...].
        # A helper function _new_annotation() can be used to generate a
        # annotation type entry data with default fields.
        # A reference to the entry should be store in both self.__elements and
        # self.__entry_dict.
        entry = self._new_annotation(type_name, begin, end)
        try:
            self.__elements[type_name].add(entry)
        except KeyError:
            self.__elements[type_name] = SortedList(key=lambda s: (s[0], s[1]))
            self.__elements[type_name].add(entry)
        tid = entry[constants.TID_INDEX]
        self.__entry_dict[tid] = entry
        return tid

    def add_link_raw(
        self, type_name: str, parent_tid: int, child_tid: int
    ) -> Tuple[int, int]:
        r"""This function adds a link entry with ``parent_tid`` and ``child_tid``
        to current data store object. Returns the ``tid`` and the ``index_id`` for
        the inserted entry in the list. This ``index_id`` is the index of the entry
        in the ``type_name`` list.

        Args:
            type_name (str):  The fully qualified type name of the new Link.
            parent_tid (int): ``tid`` of the parent entry.
            child_tid (int): ``tid`` of the child entry.

        Returns:
            ``tid`` of the entry and its index in the ``type_name`` list.

        """
        raise NotImplementedError

    def add_group_raw(
        self, type_name: str, member_type: str
    ) -> Tuple[int, int]:
        r"""This function adds a group entry with ``member_type`` to the
        current data store object. Returns the ``tid`` and the ``index_id``
        for the inserted entry in the list. This ``index_id`` is the index
        of the entry in the ``type_name`` list.

        Args:
            type_name (str): The fully qualified type name of the new Group.
            member_type (str): Fully qualified name of its members.

        Returns:
            ``tid`` of the entry and its index in the (``type_id``)th list.

        """
        raise NotImplementedError

    def set_attribute(self, tid: int, attr_name: str, attr_value: Any):
        r"""This function locates the entry data with ``tid`` and sets its
        ``attr_name`` with `attr_value`. It first finds ``attr_id``  according
        to ``attr_name``. ``tid``, ``attr_id``, and ``attr_value`` are
        passed to `set_attr()`.

        Args:
            tid (int): Unique Id of the entry.
            attr_name (str): Name of the attribute.
            attr_value (any): Value of the attribute.

        Raises:
            KeyError: when ``tid`` or ``attr_name`` is not found.
        """
        try:
            entry = self.__entry_dict[tid]
            entry_type = entry[constants.ENTRY_TYPE_INDEX]
        except KeyError as e:
            raise KeyError(f"Entry with tid {tid} not found.") from e

        try:
            attr_id = self._get_type_attribute_dict(entry_type)[attr_name]
        except KeyError as e:
            raise KeyError(f"{entry_type} has no {attr_name} attribute.") from e

        entry[attr_id] = attr_value

    def _set_attr(self, tid: int, attr_id: int, attr_value: Any):
        r"""This function locates the entry data with ``tid`` and sets its
        attribute ``attr_id``  with value `attr_value`. Called by
        `set_attribute()`.

        Args:
            tid (int): The unique id of the entry.
            attr_id (int): The id of the attribute.
            attr_value (any): The value of the attribute.
        """
        entry = self.__entry_dict[tid]
        entry[attr_id] = attr_value

    def get_attribute(self, tid: int, attr_name: str) -> Any:
        r"""This function finds the value of ``attr_name`` in entry with
        ``tid``. It locates the entry data with ``tid`` and finds `attr_id`
        of its attribute ``attr_name``. ``tid`` and ``attr_id``  are passed
        to ``get_attr()``.

        Args:
            tid (int): Unique id of the entry.
            attr_name (str): Name of the attribute.

        Returns:
            The value of ``attr_name`` for the entry with ``tid``.

        Raises:
            KeyError: when ``tid`` or ``attr_name`` is not found.
        """
        try:
            entry = self.__entry_dict[tid]
            entry_type = entry[constants.ENTRY_TYPE_INDEX]
        except KeyError as e:
            raise KeyError(f"Entry with tid {tid} not found.") from e

        try:
            attr_id = self._get_type_attribute_dict(entry_type)[attr_name]
        except KeyError as e:
            raise KeyError(f"{entry_type} has no {attr_name} attribute.") from e

        return entry[attr_id]

    def _get_attr(self, tid: int, attr_id: int) -> Any:
        r"""This function locates the entry data with ``tid`` and gets the value
        of ``attr_id``  of this entry. Called by `get_attribute()`.

        Args:
            tid (int): Unique id of the entry.
            attr_id (int): The id of the attribute.

        Returns:
            The value of ``attr_id``  for the entry with ``tid``.
        """
        entry = self.__entry_dict[tid]
        return entry[attr_id]

    def delete_entry(self, tid: int):
        r"""This function locates the entry data with ``tid`` and removes it
        from the data store. This function first removes it from `__entry_dict`.

        Args:
            tid (int): Unique id of the entry.

        Raises:
            KeyError: when entry with ``tid`` is not found.
            RuntimeError: when internal storage is inconsistent.
        """
        try:
            # get `entry data` and remove it from entry_dict
            entry_data = self.__entry_dict.pop(tid)
        except KeyError as e:
            raise KeyError(
                f"The specified tid [{tid}] "
                f"does not correspond to an existing entry data "
            ) from e

        _, _, tid, type_name = entry_data[:4]
        try:
            target_list = self.__elements[type_name]
        except KeyError as e:
            raise RuntimeError(
                f"When deleting entry [{tid}], its type [{type_name}]"
                f"does not exist in current entry lists."
            ) from e
        # complexity: O(lgn)
        # if it's annotation type, use bisect to find the index
        if self._is_annotation(type_name):
            entry_index = bisect_left(target_list, entry_data)
        else:  # if it's group or link, use the index in entry_list
            entry_index = entry_data[constants.ENTRY_INDEX_INDEX]

        if (
            entry_index >= len(target_list)
            or target_list[entry_index] != entry_data
        ):
            raise RuntimeError(
                f"When deleting entry [{tid}], entry data is not found in"
                f"the target list of [{type_name}]."
            )

        self._delete_entry_by_loc(type_name, entry_index)

    def _delete_entry_by_loc(self, type_name: str, index_id: int):
        r"""It removes an entry of `index_id` by taking both the `type_id`
        and `index_id`. Called by `delete_entry()`.

        Args:
            type_id (int): The index of the list in ``self.__elements``.
            index_id (int): The index of the entry in the list.

        Raises:
            KeyError: when ``type_name`` is not found.
            IndexError: when ``index_id`` is not found.
        """
        try:
            target_list = self.__elements[type_name]
        except KeyError as e:
            raise KeyError(
                f"The specified type [{type_name}] "
                f"does not exist in current entry lists."
            ) from e
        if index_id < 0 or index_id >= len(target_list):
            raise IndexError(
                f"The specified index_id [{index_id}] of type [{type_name}]"
                f"is out of boundary for entry list of length {len(target_list)}."
            )
        target_list.pop(index_id)
        if not target_list:
            self.__elements.pop(type_name)

    def get_entry(self, tid: int) -> Tuple[List, str]:
        r"""This function finds the entry with ``tid``. It returns the entry
        and its ``type_name``.

        Args:
            tid (int): Unique id of the entry.

        Returns:
            The entry which ``tid`` corresponds to and its ``type_name``.

        Raises:
            ValueError: An error occurred when input tid is not found.
            KeyError: An error occurred when entry_type is not found.
        """
        if tid not in self.__entry_dict:
            raise ValueError(f"Entry with tid {tid} not found.")
        entry = self.__entry_dict[tid]
        entry_type = entry[constants.ENTRY_TYPE_INDEX]
        if entry_type not in self.__elements:
            raise KeyError(f"Entry of type {entry_type} is not found.")
        return entry, entry_type

    def get_entry_index(self, tid: int) -> int:
        """Look up the entry_dict with key ``tid``. Return the ``index_id`` of
        the entry.

        Args:
            tid (int): Unique id of the entry.

        Returns:
            Index of the entry which ``tid`` corresponds to in the
            ``entry_type`` list.

        Raises:
            ValueError: An error occurred when no corresponding entry is found.
        """
        entry, entry_type = self.get_entry(tid=tid)
        # If the entry is an annotation, bisect the annotation sortedlist
        # to find the entry. May use LRU cache to optimize speed.
        # Otherwise, use ``index_id`` to find the index of the entry.
        index_id = -1
        if self._is_annotation(entry_type):
            entry_list = self.__elements[entry_type]
            index_id = entry_list.bisect_left(entry)
            if (not 0 <= index_id < len(entry_list)) or (
                entry_list[index_id][constants.TID_INDEX]
                != entry[constants.TID_INDEX]
            ):
                raise ValueError(f"Entry {entry} not found in entry list.")
        else:
            index_id = entry[constants.ENTRY_INDEX_INDEX]
        return index_id

    def co_iterator_annotation_like(
        self, type_names: List[str]
    ) -> Iterator[List]:
        r"""
        Given two or more type names, iterate their entry lists from beginning to end together.

        For every single type, their entry lists are sorted by the ``begin`` and
        ``end`` fields. The ``co_iterator_annotation_like`` function will iterate those sorted lists
        together, and yield each entry in sorted order. This tasks is quite
        similar to merging several sorted list to one sorted list. We internally
        use a `MinHeap` to order the order of yielded items, and the ordering
        is determined by:

            - start index of the entry.
            - end index of the entry.
            - the index of the entry type name in input parameter ``type_names``.

        The precedence of those values indicates their priority in the min heap
        ordering.
        For example, if two entries have both the same begin and end field,
        then their order is
        decided by the order of user input type_name (the type that first
        appears in the target type list will return first).
        For entries that have the exact same `begin`, `end` and `type_name`,
        the order will be determined arbitrarily.

        Args:
            type_names (List[str]): a list of string type names

        Returns:

            An iterator of entry elements.

        """

        n = len(type_names)
        # suppose the length of type_names is N and the length of entry list of
        # one type is M
        # then the time complexity of using min-heap to iterate
        # is O(M*log(N))

        # Initialize the first entry of all entry lists
        # it avoids empty entry lists or non-existant entry list
        first_entries = []

        for tn in type_names:
            try:
                first_entries.append(self.__elements[tn][0])
            except KeyError as e:  # self.__elements[tn] will be catched here.
                raise ValueError(
                    f"Input argument `type_names` to the function contains"
                    f" a type name [{tn}], which is not recognized."
                    f" Please input available ones in this DataStore"
                    f" object: {list(self.__elements.keys())}"
                ) from e
            except IndexError as e:  # self.__elements[tn][0] will be catched here.
                raise ValueError(
                    f"Entry list of type name, {tn} which is "
                    " one list item of input argument `type_names`,"
                    " is empty. Please check data in this DataStore). "
                    " to see if empty lists are expected"
                    f" or remove {tn} from input parameter type_names"
                ) from e

        # record the current entry index for elements
        # pointers[i] is the index of entry at (i)th sorted entry lists
        pointers = [0] * n

        # compare tuple (begin, end, order of type name in input argument
        # type_names)
        # we initialize a MinHeap with the first entry of all sorted entry lists
        # in self.__elements
        # the metric of comparing entry order is represented by the tuple
        # (begin index of entry, end index of entry,
        # the index of the entry type name in input parameter ``type_names``)
        h: List[Tuple[Tuple[int, int, int], str]] = []
        for p_idx in range(n):
            entry_tuple = (
                (
                    first_entries[p_idx][constants.BEGIN_INDEX],
                    first_entries[p_idx][constants.END_INDEX],
                    p_idx,
                ),
                first_entries[p_idx][constants.ENTRY_TYPE_INDEX],
            )
            heappush(
                h,
                entry_tuple,
            )

        while h:
            # NOTE: we push the ordering tuple to the heap
            # but not the actual entry. But we can retrieve
            # the entry by the tuple's data. Therefore,
            # in some sense, the ordering tuple represents the entry.

            # In the following comments,
            # `the current entry` means the entry that
            #      popped entry_tuple represents.
            # `the current entry list` means the entry
            # list (values of self.__elements) where `the current entry`
            # locates at.

            # retrieve the popped entry tuple (minimum item in the heap)
            # and get the p_idx (the index of the current entry list in self.__elements)
            entry_tuple = heappop(h)
            (_, _, p_idx), type_name = entry_tuple
            # get the index of current entry
            # and locate the entry represented by the tuple for yielding
            pointer = pointers[p_idx]
            entry = self.__elements[type_name][pointer]
            # check whether there is next entry in the current entry list
            # if there is, then we push the new entry's tuple into the heap
            if pointer + 1 < len(self.__elements[type_name]):
                pointers[p_idx] += 1
                new_pointer = pointers[p_idx]
                new_entry = self.__elements[type_name][new_pointer]
                new_entry_tuple = (
                    (
                        new_entry[constants.BEGIN_INDEX],
                        new_entry[constants.END_INDEX],
                        p_idx,
                    ),
                    new_entry[constants.ENTRY_TYPE_INDEX],
                )
                heappush(
                    h,
                    new_entry_tuple,
                )
            yield entry

    def get(
        self, type_name: str, include_sub_type: bool = True
    ) -> Iterator[List]:
        r"""This function fetches entries from the data store of
        type ``type_name``.

        Args:
            type_name (str): The fully qualified name of the entry.
            include_sub_type: A boolean to indicate whether get its subclass.

        Returns:
            An iterator of the entries matching the provided arguments.
        """
        if include_sub_type:
            entry_class = get_class(type_name)
            all_types = []
            # iterate all classes to find subclasses
            for type in self.__elements:
                if issubclass(get_class(type), entry_class):
                    all_types.append(type)
            for type in all_types:
                for entry in self.__elements[type]:
                    yield entry
        else:
            try:
                entries = self.__elements[type_name]
            except KeyError as e:
                raise KeyError(f"type {type_name} does not exist") from e
            for entry in entries:
                yield entry

    def next_entry(self, tid: int) -> Optional[List]:
        r"""Get the next entry of the same type as the ``tid`` entry.
        Call ``get_entry()`` to find the current index and use it to find
        the next entry. If it is a non-annotation type, it will be sorted in
        the insertion order, which means ``next_entry`` would return the next
        inserted entry.

        Args:
            tid (int): Unique id of the entry.

        Returns:
            A list of attributes representing the next entry of the same type
            as the ``tid`` entry. Return `None` when accessing the next entry
            of the last element in entry list.

        Raises:
            IndexError: An error occurred accessing index out out of entry list.
        """
        _, entry_type = self.get_entry(tid=tid)
        index_id: int = self.get_entry_index(tid=tid)
        entry_list = self.__elements[entry_type]
        if not 0 <= index_id < len(entry_list):
            raise IndexError(
                f"Index id ({index_id})) is out of bounds of the entry list."
            )
        elif index_id == len(entry_list) - 1:
            return None
        return entry_list[index_id + 1]

    def prev_entry(self, tid: int) -> Optional[List]:
        r"""Get the previous entry of the same type as the ``tid`` entry.
        Call ``get_entry()`` to find the current index and use it to find
        the previous entry. If it is a non-annotation type, it will be sorted
        in the insertion order, which means ``prev_entry`` would return the
        previous inserted entry.

        Args:
            tid (int): Unique id of the entry.

        Returns:
            A list of attributes representing the previous entry of the same
            type as the ``tid`` entry. Return `None` when accessing the previous
            entry of the first element in entry list.

        Raises:
            IndexError: An error occurred accessing index out out of entry list.
        """
        _, entry_type = self.get_entry(tid=tid)
        index_id: int = self.get_entry_index(tid=tid)
        entry_list = self.__elements[entry_type]
        if not 0 <= index_id < len(entry_list):
            raise IndexError(
                f"Index id ({index_id})) is out of bounds of the entry list."
            )
        elif index_id == 0:
            return None
        return entry_list[index_id - 1]

    def _parse_onto_file(self):
        r"""This function will populate the types and attributes used in the data_store
        with an ontology specification file. If a user provides a customized ontology
        specification file, forte will parse this file and set the internal dictionary
        ``self._type_attributes`` to store type name, parent entry, and its attribute
        information accordingly.

        For every ontology, this function will import paths containing its parent entry and
        merge all classes contained in the imported file into the dictionary. For example,
        if an ontology has a parent entry in ``ft.onto.base_ontology``, all classes in
        ``ft.onto.base_ontology`` will be imported and stored in the internal dictionary.
        A user can use classes both in the ontology specification file and their parent
        entries's paths.
        """
        if self._onto_file_path is None:
            return
        raise NotImplementedError

    def _get_entry_attributes_by_class(
        self, input_entry_class_name: str
    ) -> List:
        """Get type attributes by class name. `input_entry_class_name` should be
        a fully qualified name of an entry class.

        The `dataclass` module<https://docs.python.org/3/library/dataclasses.html> can add
        generated special methods to user-defined classes. There is an in-built function
        called `__dataclass_fields__` that is called on the class object, and it returns
        all the fields the class contains.

        .. note::

            This function is only applicable to classes decorated as Python
            `dataclass` since it relies on the `__dataclass_fields__` to find out the attributes.


        Args:
            input_entry_class_name: A fully qualified name of an entry class.

        Returns:
            A list of attributes corresponding to the input class.

        For example, for Sentence we want to get a list of
        ["speaker", "part_id", "sentiment", "classification", "classifications"].
        The solution looks like the following:

        .. code-block:: python

            # input can be a string
            entry_name = "ft.onto.base_ontology.Sentence"

            # function signature
            get_entry_attributes_by_class(entry_name)

            # return
            # ["speaker", "part_id", "sentiment", "classification", "classifications"]

        """
        class_ = get_class(input_entry_class_name)
        try:
            return list(class_.__dataclass_fields__.keys())
        except AttributeError:
            return []
