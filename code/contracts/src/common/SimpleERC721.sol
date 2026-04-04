// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

error ERC721NonexistentToken(uint256 tokenId);
error ERC721InvalidSender(address sender);
error ERC721InsufficientApproval(address sender, uint256 tokenId);

abstract contract SimpleERC721 {
    string public name;
    string public symbol;

    mapping(uint256 => address) private _ownerOf;
    mapping(address => uint256) private _balanceOf;
    mapping(uint256 => address) private _tokenApprovals;
    mapping(address => mapping(address => bool)) private _operatorApprovals;

    event Transfer(address indexed from, address indexed to, uint256 indexed tokenId);
    event Approval(address indexed owner, address indexed approved, uint256 indexed tokenId);
    event ApprovalForAll(address indexed owner, address indexed operator, bool approved);

    constructor(string memory tokenName, string memory tokenSymbol) {
        name = tokenName;
        symbol = tokenSymbol;
    }

    function ownerOf(uint256 tokenId) public view returns (address owner) {
        owner = _ownerOf[tokenId];
        if (owner == address(0)) {
            revert ERC721NonexistentToken(tokenId);
        }
    }

    function balanceOf(address owner) public view returns (uint256) {
        require(owner != address(0), "ZERO_OWNER");
        return _balanceOf[owner];
    }

    function getApproved(uint256 tokenId) public view returns (address) {
        ownerOf(tokenId);
        return _tokenApprovals[tokenId];
    }

    function isApprovedForAll(address owner, address operator) public view returns (bool) {
        return _operatorApprovals[owner][operator];
    }

    function approve(address to, uint256 tokenId) public {
        address owner = ownerOf(tokenId);
        require(msg.sender == owner || isApprovedForAll(owner, msg.sender), "NOT_AUTHORIZED");
        _tokenApprovals[tokenId] = to;
        emit Approval(owner, to, tokenId);
    }

    function setApprovalForAll(address operator, bool approved) public {
        _operatorApprovals[msg.sender][operator] = approved;
        emit ApprovalForAll(msg.sender, operator, approved);
    }

    function transferFrom(address from, address to, uint256 tokenId) public {
        if (!_isApprovedOrOwner(msg.sender, tokenId)) {
            revert ERC721InsufficientApproval(msg.sender, tokenId);
        }

        _transfer(from, to, tokenId);
    }

    function safeTransferFrom(address from, address to, uint256 tokenId) public {
        transferFrom(from, to, tokenId);
    }

    function safeTransferFrom(address from, address to, uint256 tokenId, bytes calldata) public {
        transferFrom(from, to, tokenId);
    }

    function tokenURI(uint256 tokenId) public view virtual returns (string memory);

    function _mint(address to, uint256 tokenId) internal {
        require(to != address(0), "ZERO_TO");
        require(_ownerOf[tokenId] == address(0), "ALREADY_MINTED");

        _beforeTokenTransfer(address(0), to, tokenId);

        _balanceOf[to] += 1;
        _ownerOf[tokenId] = to;

        emit Transfer(address(0), to, tokenId);
    }

    function _transfer(address from, address to, uint256 tokenId) internal {
        address owner = ownerOf(tokenId);
        if (owner != from) {
            revert ERC721InvalidSender(from);
        }
        require(to != address(0), "ZERO_TO");

        _beforeTokenTransfer(from, to, tokenId);

        delete _tokenApprovals[tokenId];

        unchecked {
            _balanceOf[from] -= 1;
            _balanceOf[to] += 1;
        }

        _ownerOf[tokenId] = to;

        emit Transfer(from, to, tokenId);
    }

    function _exists(uint256 tokenId) internal view returns (bool) {
        return _ownerOf[tokenId] != address(0);
    }

    function _isApprovedOrOwner(address sender, uint256 tokenId) internal view returns (bool) {
        address owner = ownerOf(tokenId);
        return sender == owner || getApproved(tokenId) == sender || isApprovedForAll(owner, sender);
    }

    function _beforeTokenTransfer(address from, address to, uint256 tokenId) internal virtual {
        from;
        to;
        tokenId;
    }
}
